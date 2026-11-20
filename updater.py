"""Install official desktop releases in a per-user cache and supervise their local server."""
from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from http.client import HTTPConnection, HTTPException
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, Request
import zipfile

from console_log import event

ROOT = Path(__file__).resolve().parent
REPOSITORY = "NolReaction/quizlens"
RELEASES = "https://github.com/" + REPOSITORY + "/releases"
RESTART = 75
MAX_ZIP = 128 * 1024 * 1024


def platform_asset():
    return {"win32": "QuizLens-Windows.zip", "darwin": "QuizLens-Mac.zip"}.get(sys.platform)


def version_key(version):
    if not isinstance(version, str) or not re.fullmatch(r"(?:0|[1-9]\d{0,5})(?:\.(?:0|[1-9]\d{0,5})){2}", version, re.ASCII):
        raise ValueError("Некорректная версия релиза.")
    return tuple(map(int, version.split(".")))


def read_json(path):
    try:
        value = json.loads(path.read_bytes())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def install_lock(cache):
    # Released automatically by the OS on crash, shared by servers using different ports.
    with (cache / "install.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if not lock.tell():
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            def acquire():
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            def acquire():
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + 30
        while True:
            try:
                acquire()
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                    raise RuntimeError("Другой экземпляр QuizLens занят установкой. Повтори чуть позже.") from exc
                time.sleep(.12)
        yield


def official_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in
            {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}):
        raise ValueError("Адрес загрузки не принадлежит GitHub Releases.")


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_official(url, limit):
    official_url(url)
    req = Request(url, headers={"User-Agent": "QuizLens-Update", "Cache-Control": "no-cache"})
    with build_opener(ReleaseRedirect()).open(req, timeout=20) as response:
        official_url(response.url)
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Файл обновления превышает допустимый размер.")
    return data


def latest_release(asset):
    manifest = json.loads(fetch_official(RELEASES + "/latest/download/release.json", 32768))
    if not isinstance(manifest, dict):
        raise ValueError("Некорректный манифест релиза.")
    version = manifest.get("version")
    version_key(version)
    sums = manifest.get("sha256")
    if (manifest.get("repository") != REPOSITORY or manifest.get("tag") != "v" + version or
            not isinstance(sums, dict) or not re.fullmatch(r"[a-f0-9]{64}", str(sums.get(asset, "")))):
        raise ValueError("Не удалось проверить происхождение релиза и его SHA-256.")
    return version, sums[asset]


def core(folder, asset):
    return folder / ("QuizLens-Mac/QuizLens.app/Contents/Resources/quiz-lens" if asset == "QuizLens-Mac.zip"
                     else "QuizLens-Windows/app")


def valid_core(root, version):
    return ((root / "app.py").is_file() and (root / "updater.py").is_file() and (root / "ui/index.html").is_file()
            and (root / "packaging/VERSION").read_text(encoding="ascii").strip() == version
            and (root / "packaging/REPOSITORY").read_text(encoding="ascii").strip() == REPOSITORY)


def validate_entries(bundle, folder):
    entries = bundle.infolist()
    if len(entries) > 5000 or sum(entry.file_size for entry in entries) > 300 * 1024 * 1024:
        raise ValueError("Архив обновления слишком большой.")
    seen = set()
    for entry in entries:
        name = entry.filename.rstrip("/")
        parts = name.split("/")
        typ = stat.S_IFMT(entry.external_attr >> 16)
        if (entry.orig_filename != entry.filename or parts[0] not in {folder, "__MACOSX"} or
                any(not re.fullmatch(r"[A-Za-z0-9_. -]+", part) or part.endswith((".", " ")) or
                    re.fullmatch(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part, re.I)
                    for part in parts) or name.casefold() in seen or
                typ not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1):
            raise ValueError("В архиве обновления есть недопустимый путь или тип файла.")
        seen.add(name.casefold())


def stage_release(cache, version, digest, asset):
    version_key(version)
    folders = cache / "versions"
    folders.mkdir(parents=True, exist_ok=True)
    target = folders / version
    with install_lock(cache):
        if target.exists():
            if read_json(target / "verified.json").get("sha256") == digest and valid_core(core(target, asset), version):
                return
            raise ValueError("Локальная копия этой версии отличается от релиза.")
        data = fetch_official(f"{RELEASES}/download/v{version}/{asset}", MAX_ZIP)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("SHA-256 скачанного архива отличается от манифеста релиза.")
        with tempfile.TemporaryDirectory(dir=folders, prefix="loading-") as tmp:
            work = Path(tmp)
            extracted = work / "payload"
            extracted.mkdir()
            with zipfile.ZipFile(io.BytesIO(data)) as bundle:
                validate_entries(bundle, asset[:-4])
                if asset == "QuizLens-Windows.zip":
                    bundle.extractall(extracted)
                else:
                    # Preserve executable bits, sealed bundle and signed resource forks.
                    archive = work / asset
                    archive.write_bytes(data)
                    subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(extracted)], check=True, timeout=90)
                    app = extracted / "QuizLens-Mac/QuizLens.app"
                    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", "--all-architectures",
                                    str(app)], check=True, timeout=45)
                    # If the running app has a Developer ID, pin the new bundle to the same team.
                    old_app = ROOT.parents[2]
                    if old_app.suffix == ".app":
                        sig = subprocess.run(["/usr/bin/codesign", "-dv", "--verbose=4", str(old_app)],
                                             capture_output=True, text=True, timeout=10)
                        if sig.returncode:
                            raise ValueError("Не удалось проверить подпись установленного приложения.")
                        team = re.search(r"^TeamIdentifier=([A-Z0-9]{10})$", sig.stderr, re.M)
                        if team:
                            requirement = 'anchor apple generic and certificate leaf[subject.OU] = "' + team[1] + '"'
                            subprocess.run(["/usr/bin/codesign", "--verify", "-R=" + requirement, str(app)],
                                           check=True, timeout=30)
                            subprocess.run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(app)],
                                           check=True, timeout=45)
            if not valid_core(core(extracted, asset), version):
                raise ValueError("В скачанной сборке отсутствуют файлы QuizLens или отличается версия.")
            write_json(extracted / "verified.json", {"sha256": digest, "version": version})
            extracted.rename(target)


class Updates:
    def __init__(self, version, shutdown, busy=lambda: False):
        self.current = version
        env = os.environ.get("QUIZLENS_UPDATE_SESSION")
        self.session = Path(env) if env and platform_asset() else None
        self.cache = self.session.parent if self.session else None
        self.automatic = bool(self.cache and read_json(self.cache / "preferences.json").get("automatic", True))
        self.lock = threading.RLock()
        self.shutdown = shutdown
        self.busy = busy
        self.active_requests = 0
        self.checked = 0
        self.latest = ""
        self.digest = ""
        self.restarting = False
        self.state = "idle" if self.cache else "unsupported"
        self.message = "" if self.cache else "Обновление из приложения доступно в официальной сборке для Windows и Mac."
        if self.cache:
            rejected = read_json(self.cache / "rejected.json")
            if rejected.get("version") and rejected.get("restored") == self.current:
                self.state = "error"
                self.message = rejected["message"]
                self.checked = time.monotonic()

    def snapshot(self, *, periodic=True):
        with self.lock:
            if (periodic and self.automatic and self.state in {"idle", "current", "error", "available"}
                    and (not self.checked or time.monotonic() - self.checked > 21600)):
                self.start_check()
            return {"supported": bool(self.cache), "currentVersion": self.current, "latestVersion": self.latest,
                    "state": self.state, "automatic": self.automatic, "message": self.message}

    def start_check(self, manual=False):
        self.checked = time.monotonic()
        self.state, self.message = "checking", "Проверяю релизы GitHub…"
        threading.Thread(target=self.run, args=(self.check, manual), daemon=True).start()

    def run(self, task, *args):
        try:
            task(*args)
        except Exception as exc:
            with self.lock:
                self.state = "error"
                self.checked = time.monotonic()
                self.message = "Не удалось обновить QuizLens. " + (
                    str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Проверь соединение и попробуй снова.")
            event("Обновления", "Не удалось завершить обновление. Подробности в меню обновлений.",
                  level="ВНИМАНИЕ", error=exc, repeat_after=60)

    def check(self, manual):
        version, digest = latest_release(platform_asset())
        with self.lock:
            self.latest, self.digest = version, digest
            rejected = read_json(self.cache / "rejected.json")
            if version_key(version) <= version_key(self.current):
                self.state, self.message = "current", "Установлена последняя версия."
            elif version == rejected.get("version") and not manual:
                self.state, self.message = "error", rejected.get("message", "Предыдущая попытка обновления не удалась.")
            elif read_json(self.cache / "versions" / version / "verified.json").get("sha256") == digest:
                self.state, self.message = "ready", f"Версия {version} готова к запуску."
            else:
                self.state, self.message = "available", f"Доступна версия {version}."

    def download(self):
        event("Обновления", "Загружается новая версия.")
        stage_release(self.cache, self.latest, self.digest, platform_asset())
        with self.lock:
            self.state, self.message = "ready", f"Версия {self.latest} готова к запуску."
        event("Обновления", "Новая версия проверена и готова к запуску.")

    @contextlib.contextmanager
    def work(self):
        with self.lock:
            if self.restarting:
                raise RuntimeError("QuizLens запускает новую версию. Повтори через несколько секунд.")
            self.active_requests += 1
        try:
            yield
        finally:
            with self.lock:
                self.active_requests -= 1

    def command(self, data):
        with self.lock:
            if not self.cache:
                return self.snapshot(periodic=False)
            action = data.get("action")
            if action == "preferences":
                enabled = data.get("automatic")
                if type(enabled) is not bool:
                    raise ValueError("Параметр автообновления должен быть логическим.")
                write_json(self.cache / "preferences.json", {"automatic": enabled})
                self.automatic = enabled
            elif action == "check":
                if self.state not in {"checking", "downloading", "restarting"}:
                    self.start_check(manual=True)
            elif action == "apply":
                if self.state == "available":
                    self.state, self.message = "downloading", "Загружаю и проверяю обновление…"
                    threading.Thread(target=self.run, args=(self.download,), daemon=True).start()
            elif action == "activate":
                if self.state == "ready":
                    if self.active_requests or self.busy():
                        self.message = "Обновление готово. Дожидаюсь завершения текущей операции."
                        return self.snapshot(periodic=False)
                    write_json(self.session / "requested.json", {"version": self.latest, "sha256": self.digest})
                    self.state, self.message = "restarting", "Переключаю QuizLens на новую версию…"
                    self.restarting = True
                    threading.Thread(target=self.shutdown, daemon=True).start()
            else:
                raise ValueError("Неизвестное действие обновления.")
            return self.snapshot(periodic=False)

    def ready(self, port):
        if self.session:
            write_json(self.session / "ready.json", {"version": self.current, "port": port})


def target_root(cache, version, asset):
    try:
        version_key(version)
        target = cache / "versions" / version
        if read_json(target / "verified.json").get("version") == version and valid_core(core(target, asset), version):
            return core(target, asset)
    except (ValueError, OSError):
        pass
    return None


def start_server(root, args, session, port, reopen):
    env = {**os.environ, "QUIZLENS_UPDATE_SESSION": str(session), "PYTHONDONTWRITEBYTECODE": "1"}
    if platform_asset() == "QuizLens-Mac.zip":
        helpers = root.parents[1] / "Helpers"
        env.update(QUIZ_LENS_REGION_OCR=str(helpers / "RegionOCR"), QUIZLENS_PDF_READER=str(helpers / "ReadPDF"),
                   QUIZLENS_WINDOW_READER=str(helpers / "WindowReader"))
    command = [sys.executable]
    if sys.flags.ignore_environment:
        command.append("-E")
    if sys.flags.no_user_site:
        command.append("-s")
    command += ["-B", "-u", str(root / "app.py"), "--data-dir", str(args.data_dir.resolve()), "--port", str(port)]
    if args.no_browser or not reopen:
        command.append("--no-browser")
    return subprocess.Popen(command, cwd=root, env=env,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)


def stop_child(child):
    if child.poll() is None:
        try:
            child.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            child.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            child.kill()
            child.wait()


def healthy(session, version):
    notice = read_json(session / "ready.json")
    if notice.get("version") != version or not isinstance(notice.get("port"), int):
        return False
    probe = HTTPConnection("127.0.0.1", notice["port"], timeout=.6)
    try:
        probe.request("GET", "/api/health")
        with probe.getresponse() as response:
            value = json.loads(response.read(4096))
            return response.status == 200 and value.get("app") == "QuizLens" and value.get("version") == version
    except (OSError, ValueError, EOFError, HTTPException):
        return False
    finally:
        probe.close()


def supervise():
    import app
    args = app.launch_arguments()
    asset = platform_asset()
    base = app.VERSION
    try:
        supported = asset and valid_core(ROOT, base)
    except (ValueError, OSError):
        supported = False
    if not supported:
        return app.main()
    cache = args.data_dir.resolve() / "runtime" / "updates"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = read_json(cache / "active.json").get("version")
    start = target_root(cache, current, asset)
    if not start or version_key(current) <= version_key(base):
        start, current = ROOT, base
    port = args.port
    with tempfile.TemporaryDirectory(dir=cache, prefix="session-") as temp:
        session = Path(temp)
        ready_file, request_file = session / "ready.json", session / "requested.json"
        child = None
        def terminate(signum, frame):
            raise KeyboardInterrupt
        previous_handler = signal.signal(signal.SIGTERM, terminate)
        try:
            child = start_server(start, args, session, port, True)
            while True:
                code = child.wait()
                if code != RESTART:
                    return code
                request = read_json(request_file)
                request_file.unlink(missing_ok=True)
                version = request.get("version")
                candidate = target_root(cache, version, asset)
                port = read_json(ready_file).get("port") or port
                ready_file.unlink(missing_ok=True)
                try:
                    if not candidate or version_key(version) <= version_key(current):
                        raise ValueError("Новая сборка не прошла проверку перед запуском.")
                    with install_lock(cache):
                        active = read_json(cache / "active.json").get("version")
                        if active and version_key(version) < version_key(active):
                            raise ValueError("Другой экземпляр QuizLens уже установил более новую версию.")
                        marker = read_json(cache / "versions" / version / "verified.json")
                        if marker.get("sha256") != request.get("sha256"):
                            raise ValueError("Контрольная сумма сохранённой сборки изменилась.")
                        child = start_server(candidate, args, session, port, False)
                        deadline = time.monotonic() + 25
                        while child.poll() is None and time.monotonic() < deadline:
                            if healthy(session, version):
                                break
                            time.sleep(.15)
                        else:
                            raise RuntimeError("Локальный сервер не подтвердил запуск.")
                        write_json(cache / "active.json", {"version": version})
                        (cache / "rejected.json").unlink(missing_ok=True)
                        start, current = candidate, version
                        event("Обновления", f"QuizLens {version} запущен и проверен.")
                except (OSError, ValueError, RuntimeError) as exc:
                    stop_child(child)
                    ready_file.unlink(missing_ok=True)
                    message = f"Версия {version} не запустилась. Восстановлена предыдущая версия {current}."
                    write_json(cache / "rejected.json", {"version": version, "restored": current, "message": message})
                    event("Обновления", "Новая версия не запустилась. Возвращаюсь к предыдущей.",
                          level="ВНИМАНИЕ", error=exc)
                    child = start_server(start, args, session, port, False)
        except KeyboardInterrupt:
            return 0
        finally:
            try:
                if child:
                    stop_child(child)
            finally:
                signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    sys.exit(supervise())
