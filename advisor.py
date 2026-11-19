"""Local hardware inventory and conservative starting points for timed text questions."""

import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys


REQUIREMENTS = "https://lmstudio.ai/docs/app/system-requirements"
# File sizes shown by Hugging Face are decimal GB. RAM and VRAM below are GiB.
_MODELS = {
    "small": ("Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
              "qwen2.5-1.5b-instruct-q4_k_m.gguf", "1,12 ГБ"),
    "medium": ("Qwen3-4B-Instruct-2507", "lmstudio-community/Qwen3-4B-Instruct-2507-GGUF",
               "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "2,50 ГБ"),
    "large": ("Qwen2.5-7B-Instruct", "bartowski/Qwen2.5-7B-Instruct-GGUF",
              "Qwen2.5-7B-Instruct-Q4_K_M.gguf", "4,68 ГБ"),
}


def _command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=1.5,
                                check=False, **({"creationflags": subprocess.CREATE_NO_WINDOW}
                                                if os.name == "nt" else {}))
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _bytes_to_gib(value):
    try:
        size = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return round(size / (1 << 30), 1) if 0 < size <= (65536 << 30) else None


def _windows_ram():
    from ctypes import Structure, WinDLL, byref, c_uint32, c_uint64, sizeof

    class MemoryStatus(Structure):
        _fields_ = [(name, c_uint32) for name in ("length", "load")] + [
            (name, c_uint64) for name in ("total", "available", "pageTotal", "pageFree",
                                           "virtualTotal", "virtualFree", "extended")]

    status = MemoryStatus()
    status.length = sizeof(status)
    if WinDLL("kernel32").GlobalMemoryStatusEx(byref(status)):
        return _bytes_to_gib(status.total)
    return None


def _windows_cpu():
    import winreg

    path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        return platform.processor()


def _linux_cpu():
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as info:
            for line in info:
                key, _, value = line.partition(":")
                if key.strip() in ("model name", "Hardware") and value.strip():
                    return value.strip()
    except OSError:
        pass
    return platform.processor()


def _nvidia():
    binary = shutil.which("nvidia-smi")
    if not binary:
        return "", None
    listing = _command([binary, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    devices = []
    for line in listing.splitlines():
        name, separator, mib = line.rpartition(",")
        try:
            memory = _bytes_to_gib(int(mib.strip()) << 20)
        except ValueError:
            continue
        if separator and name.strip() and memory:
            devices.append((memory, name.strip()))
    if not devices:
        return "", None
    # The default advice targets a single GPU and never sums multiple cards.
    vram, name = max(devices)
    return name, vram


def _windows_avx2():
    # PF_AVX2_INSTRUCTIONS_AVAILABLE was added with Windows 10 build 19041.
    try:
        if sys.getwindowsversion().build < 19041:
            return None
        from ctypes import WinDLL, c_int, c_uint32
        present = WinDLL("kernel32", winmode=0x800).IsProcessorFeaturePresent
        present.argtypes, present.restype = [c_uint32], c_int
        return bool(present(40))
    except (ImportError, AttributeError, OSError):
        return None


def _dxcore_gpu():
    """Called only in a bounded child. Interface layout follows Microsoft's dxcore_interface.h."""
    import ctypes as c
    import uuid
    from contextlib import contextmanager

    pointer = c.c_void_p
    uint32 = c.c_uint32
    hresult = c.c_int32
    out_pointer = c.POINTER(pointer)

    def guid(value):
        return (c.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le)

    def method(obj, slot, returns, *types):
        vtable = c.cast(obj, c.POINTER(c.POINTER(pointer))).contents
        return c.WINFUNCTYPE(returns, pointer, *types)(vtable[slot])

    @contextmanager
    def owned():
        obj = pointer()
        try:
            yield obj
        finally:
            if obj:
                method(obj, 2, uint32)(obj)  # IUnknown::Release

    factory_iid = guid("78ee5945-c36e-4b13-a669-005dd11c0f06")
    list_iid = guid("526c7776-40e9-459b-b711-f32ad76dfc28")
    adapter_iid = guid("f0db4c7f-fe5a-42a2-bd62-f2a6cf6fc83e")
    graphics = guid("0c9ece4d-2f6e-4f01-8c96-e89e331b47b1")  # D3D12 graphics, excludes NPUs.
    create = c.WinDLL("dxcore", winmode=0x800).DXCoreCreateAdapterFactory
    create.argtypes, create.restype = [pointer, out_pointer], hresult
    best = ("", 0)
    with owned() as factory, owned() as adapters:
        if create(factory_iid, c.byref(factory)) < 0 or not factory:
            return best
        make_list = method(factory, 3, hresult, uint32, pointer, pointer, out_pointer)
        if make_list(factory, 1, graphics, list_iid, c.byref(adapters)) < 0 or not adapters:
            return best
        count = method(adapters, 4, uint32)(adapters)
        get_adapter = method(adapters, 3, hresult, uint32, pointer, out_pointer)
        for index in range(min(count, 64)):
            with owned() as adapter:
                if get_adapter(adapters, index, adapter_iid, c.byref(adapter)) < 0 or not adapter:
                    continue
                get = method(adapter, 6, hresult, uint32, c.c_size_t, pointer)
                hardware, integrated, size = c.c_bool(), c.c_bool(), c.c_uint64()
                # DXCoreAdapterProperty: IsHardware, IsIntegrated, DedicatedAdapterMemory.
                if (get(adapter, 11, c.sizeof(hardware), c.byref(hardware)) < 0 or not hardware.value or
                        get(adapter, 12, c.sizeof(integrated), c.byref(integrated)) < 0 or integrated.value or
                        get(adapter, 7, c.sizeof(size), c.byref(size)) < 0 or size.value <= best[1]):
                    continue
                name = "Дискретная видеокарта"
                length = c.c_size_t()
                get_size = method(adapter, 7, hresult, uint32, c.POINTER(c.c_size_t))
                # DriverDescription is a null-terminated UTF-8 string.
                if get_size(adapter, 2, c.byref(length)) >= 0 and 0 < length.value <= 1024:
                    buffer = c.create_string_buffer(length.value)
                    if get(adapter, 2, length.value, buffer) >= 0:
                        name = buffer.value.decode("utf-8", "replace").strip()[:200] or name
                best = (name, size.value)
    return best


def _windows_gpu():
    if os.name != "nt":
        return "", None
    # A display driver can take longer than expected. Keep faults or hangs outside the server.
    output = _command([sys.executable, "-I", "-S", os.path.abspath(__file__), "--windows-gpu"])
    try:
        name, size = json.loads(output)
        if isinstance(name, str) and name and type(size) is int and _bytes_to_gib(size):
            return name[:200], _bytes_to_gib(size)
    except (TypeError, ValueError):
        pass
    return "", None


def detect_hardware():
    system = {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(platform.system(), "unknown")
    profile = {"platform": system, "arch": platform.machine(), "cpuName": "", "gpuName": "",
               "accelerator": "unknown", "ramGiB": None, "vramGiB": None}
    if system == "macos":
        profile["cpuName"] = _command(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"])
        profile["ramGiB"] = _bytes_to_gib(_command(["/usr/sbin/sysctl", "-n", "hw.memsize"]))
        if (profile["arch"].lower() in ("arm64", "aarch64") or profile["cpuName"].startswith("Apple ") or
                _command(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"]) == "1"):
            profile.update(accelerator="apple", arch="arm64", gpuName="Apple Silicon")
        return profile
    if system == "windows":
        if profile["arch"].lower() in ("x86_64", "amd64"):
            profile["avx2"] = _windows_avx2()
        try:
            profile["ramGiB"] = _windows_ram()
            profile["cpuName"] = _windows_cpu()
        except (ImportError, OSError, AttributeError):
            pass
    elif system == "linux":
        profile["cpuName"] = _linux_cpu()
        try:
            profile["ramGiB"] = _bytes_to_gib(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError):
            pass
    if system in ("windows", "linux"):
        gpu, vram = _windows_gpu() if system == "windows" else ("", None)
        if vram is None:
            gpu, vram = _nvidia()
        profile["gpuName"], profile["vramGiB"] = gpu, vram
        if profile["vramGiB"]:
            profile["accelerator"] = "discrete"
    return profile


def _memory(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not 0 < value <= 65536 or not math.isfinite(value):
        raise ValueError("Укажи объём памяти в ГиБ или оставь поле пустым.")
    return value


def _profile(raw):
    if not isinstance(raw, dict):
        raise ValueError("Проверь параметры компьютера.")
    system = raw.get("platform", "unknown")
    accelerator = raw.get("accelerator", "unknown")
    if system not in ("macos", "windows", "linux", "unknown") or accelerator not in (
            "apple", "discrete", "cpu", "unknown"):
        raise ValueError("Выбери платформу и тип графики из списка.")
    if accelerator == "apple" and system not in ("macos", "unknown"):
        raise ValueError("Для общей памяти Apple Silicon выбери macOS.")
    details = {key: raw.get(key, "") for key in ("arch", "cpuName", "gpuName")}
    if any(not isinstance(value, str) or len(value) > 256 for value in details.values()):
        raise ValueError("Проверь описание компьютера.")
    avx2 = raw.get("avx2")
    if avx2 is not None and type(avx2) is not bool:
        raise ValueError("Проверь параметр AVX2.")
    ram, vram = _memory(raw.get("ramGiB")), _memory(raw.get("vramGiB"))
    return {**details, "platform": system, "accelerator": accelerator, "ramGiB": ram, "avx2": avx2,
            "vramGiB": vram if accelerator == "discrete" else None}


def _installed(model, models):
    # Exact family names plus known low-bit or unqualified IDs exposed by /v1/models.
    # Do not match similarly named thinking/vision models, users' fine-tunes or large quants.
    base = re.escape(model.casefold())
    pattern = re.compile(base + r"(?:[-_]gguf)?(?:[-_: @]+(?:q4[_-]k[_-]m|4[-_]?bit))?(?:\.gguf)?(?::\d+)?$")
    for model_id in models:
        if isinstance(model_id, str) and 0 < len(model_id) <= 500:
            name = model_id.rsplit("/", 1)[-1].casefold()
            if pattern.fullmatch(name):
                return model_id
    return None


def _card(tier, reason, models):
    name, repo, file, file_size = _MODELS[tier]
    url = "https://huggingface.co/" + repo
    return {"id": name.lower(), "name": name, "quant": "Q4_K_M", "fileSize": file_size, "search": repo,
            "fileUrl": f"{url}/blob/main/{file}", "downloadUrl": f"{url}/resolve/main/{file}?download=true",
            "installedId": _installed(name, models), "reason": reason}


def recommend(profile=None, model_ids=()):
    profile = _profile(detect_hardware() if profile is None else profile)
    system, accelerator = profile["platform"], profile["accelerator"]
    ram, vram = profile["ramGiB"], profile["vramGiB"]
    result = {"profile": profile, "primary": None, "alternative": None, "installedMatch": None,
              "needsHardware": False, "notes": [], "requirementsUrl": REQUIREMENTS}
    notes = result["notes"]
    if system == "macos" and accelerator != "apple":
        if profile["arch"].lower() in ("x86_64", "amd64", "i386", "i686", "x86"):
            notes.append("LM Studio поддерживает Mac с Apple Silicon. На Intel Mac можно пользоваться готовыми карточками QuizLens.")
        else:
            result["needsHardware"] = True
            notes.append("Проверь чип в меню Apple → Об этом Mac. Для M-серии выбери Apple Silicon. На Intel Mac доступны готовые карточки QuizLens.")
        return result
    windows_x64 = system == "windows" and profile["arch"].lower() in ("x86_64", "amd64")
    if windows_x64 and profile["avx2"] is False:
        notes.append("Windows не подтверждает поддержку AVX2, нужную для LM Studio x64. Для этого ПК подойдут готовые карточки.")
        if model_ids:
            notes.append("Если у тебя уже работает другая локальная среда с моделью, проверь её на трёх вопросах.")
        return result
    if ram is None:
        result["needsHardware"] = True
        notes.append("Для подбора модели укажи объём оперативной или объединённой памяти компьютера.")
        return result
    # Detected usable RAM may be a little below the nominal 8/16 GiB.
    if ram < 7.5:
        notes.append("При таком объёме памяти начни с готовых карточек без модели. Они оставят память браузеру и распознаванию.")
        return result

    tier = "small"
    other_tier = None
    reason = "Компактная модель оставляет больше памяти системе, браузеру и распознаванию."
    other_reason = ""
    if accelerator == "apple" and ram >= 15:
        tier = "medium"
        reason = "Объединённой памяти достаточно для первой пробы 4B с коротким контекстом."
    elif accelerator == "discrete":
        if vram is None:
            result["needsHardware"] = True
            notes.append("Введи выделенную видеопамять, чтобы уточнить выбор. Пока подобрана лёгкая модель.")
        elif ram >= 15 and vram >= 5.5:
            tier = "medium"
            reason = "Объём выделенной видеопамяти оставляет запас для короткого контекста 4B."
    elif accelerator in ("cpu", "unknown"):
        notes.append("При расчёте на процессоре начни с 1.5B и измерь скорость на вопросах. " +
                     ("Если есть отдельная видеокарта, укажи её параметры вручную." if accelerator == "unknown"
                      else "Больший объём оперативной памяти сам по себе не ускоряет ответы."))
    if tier == "medium":
        plentiful = ram >= 23 and accelerator == "apple" or accelerator == "discrete" and vram >= 7.5
        other_tier = "large" if plentiful else "small"
        other_reason = ("Есть запас для пробы 7B. Сравни правильность и время на тех же вопросах."
                        if plentiful else "Попробуй меньшую модель, если ответы 4B приходят после таймера.")
    if windows_x64 and profile["avx2"] is None:
        notes.append("Для LM Studio в Windows x64 нужен процессор с AVX2.")
    notes.append("Начни с контекста 4096 токенов. Размер файла указан для скачивания. При работе дополнительную память занимают контекст и служебные буферы.")
    result["primary"] = _card(tier, reason, model_ids)
    if other_tier:
        result["alternative"] = _card(other_tier, other_reason, model_ids)
    result["installedMatch"] = result["primary"]["installedId"]
    if result["installedMatch"]:
        notes.append("Локальный сервер видит эту модель. Если в её имени не указано сжатие, проверь его в библиотеке моделей.")
    return result


if __name__ == "__main__" and sys.argv[1:] == ["--windows-gpu"] and os.name == "nt":
    try:
        print(json.dumps(_dxcore_gpu()))
    except (OSError, AttributeError):
        print("null")
