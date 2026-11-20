"""Local hardware inventory and conservative starting points for timed text questions."""

import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


REQUIREMENTS = "https://lmstudio.ai/docs/app/system-requirements"

@dataclass(frozen=True)
class Model:
    # Context is read from the pinned GGUF header. Output limits and parameter counts
    # come from the publisher's card. An unspecified output maximum stays unknown.
    publisher: str
    repo: str
    revision: str
    filename: str
    file_bytes: int
    parameters_b: float
    context_tokens: int
    max_output_tokens: Optional[int]
    # Conservative nominal capacities for interactive use with a 4096-token context.
    # They are estimates for the full file plus runtime overhead, not model limits.
    unified_gib: int
    vram_gib: int
    description: str
    active_parameters_b: Optional[float] = None

    @property
    def name(self):
        return self.publisher.split("/")[1]


_MODELS = {
    "tiny": Model(
        "Qwen/Qwen2.5-0.5B-Instruct", "bartowski/Qwen2.5-0.5B-Instruct-GGUF",
        "41ba88dbac95fed2528c92514c131d73eb5a174b", "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf",
        397_808_192, 0.49, 32_768, 8_192, 8, 2,
        "Самый маленький файл в подборке. Подойдёт для первого запуска и коротких вопросов."),
    "small": Model(
        "Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "91cad51170dc346986eccefdc2dd33a9da36ead9", "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        1_117_320_736, 1.54, 32_768, 8_192, 8, 3,
        "Компактная модель для коротких вопросов и компьютеров с небольшим объёмом памяти."),
    "compact": Model(
        "Qwen/Qwen2.5-3B-Instruct", "bartowski/Qwen2.5-3B-Instruct-GGUF",
        "f302c64a2269a69fb27b2f9473b362f5bb8e78d8", "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        1_929_903_264, 3.09, 32_768, 8_192, 12, 4,
        "Промежуточный вариант между 1,5 и 4 млрд параметров для вопросов по тексту."),
    "medium": Model(
        "Qwen/Qwen3-4B-Instruct-2507", "lmstudio-community/Qwen3-4B-Instruct-2507-GGUF",
        "4edb920b6f14e3b9284d4502a6485103d72cde05", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        2_497_280_448, 4.0, 262_144, None, 16, 6,
        "Даёт ответ без отдельного этапа рассуждения. Удобна для вопросов с таймером."),
    "gemma4b": Model(
        "google/gemma-3-4b-it", "lmstudio-community/gemma-3-4b-it-GGUF",
        "d650fa07be1a9252c9f7c6597fadc729a377254b", "gemma-3-4b-it-Q4_K_M.gguf",
        2_489_757_856, 4, 131_072, 8_192, 16, 6,
        "Модель Google для сравнения ответов на русском. Для текста достаточно этого GGUF."),
    "large": Model(
        "Qwen/Qwen2.5-7B-Instruct", "bartowski/Qwen2.5-7B-Instruct-GGUF",
        "8911e8a47f92bac19d6f5c64a2e2095bd2f7d031", "Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        4_683_074_240, 7.61, 32_768, 8_192, 24, 8,
        "Следующий размер после 4B. Можно сравнить, как модель разбирает похожие варианты ответа."),
    "ministral": Model(
        "mistralai/Ministral-3-8B-Instruct-2512", "unsloth/Ministral-3-8B-Instruct-2512-GGUF",
        "3731507ec3e867db16d620f73e14d689125758f4", "Ministral-3-8B-Instruct-2512-Q4_K_M.gguf",
        5_198_386_720, 8.4, 262_144, None, 24, 8,
        "Текстовая часть Ministral на 8,4 млрд параметров. QuizLens передаёт ей распознанный вопрос."),
    "gemma12b": Model(
        "google/gemma-3-12b-it", "lmstudio-community/gemma-3-12b-it-GGUF",
        "785c151b0dabf00cfaff1421239fafdbb8e5995d", "gemma-3-12b-it-Q4_K_M.gguf",
        7_300_574_976, 12, 131_072, 8_192, 32, 12,
        "Более крупная Gemma для сравнения с 4B на сложных формулировках из документа."),
    "qwen14b": Model(
        "Qwen/Qwen2.5-14B-Instruct", "bartowski/Qwen2.5-14B-Instruct-GGUF",
        "05244aa5d871c661c80082a15d3bce44714d068d", "Qwen2.5-14B-Instruct-Q4_K_M.gguf",
        8_988_110_976, 14.7, 32_768, 8_192, 40, 16,
        "Крупная текстовая модель. Замерь время ответа на своём устройстве перед квизом."),
    "mixture": Model(
        "Qwen/Qwen3-30B-A3B-Instruct-2507", "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF",
        "eea7b2be5805a5f151f8847ede8e5f9a9284bf77", "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
        18_556_686_752, 30.5, 262_144, None, 48, 24,
        "На каждом токене активна часть параметров. Для загрузки в память нужен весь файл модели.", active_parameters_b=3.3),
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


_FIT_LABELS = {
    "recommended": "С чего начать", "alternative": "Ещё один вариант", "fits": "Есть запас памяти",
    "limited": "Мало запаса", "unknown": "Нужны характеристики", "unsupported": "LM Studio недоступна",
}


def _card(model, fit, reason, models):
    url = "https://huggingface.co/" + model.repo
    file_size = f"{model.file_bytes / 10 ** 9:.2f}".replace(".", ",") + " ГБ"
    return {
        "id": model.name.lower(), "name": model.name, "quant": "Q4_K_M", "fileSize": file_size,
        "fileBytes": model.file_bytes, "filename": model.filename, "search": model.repo,
        "fileUrl": f"{url}/blob/{model.revision}/{model.filename}",
        "downloadUrl": f"{url}/resolve/{model.revision}/{model.filename}?download=true",
        "sourceUrl": "https://huggingface.co/" + model.publisher,
        "parametersB": model.parameters_b, "activeParametersB": model.active_parameters_b,
        "contextTokens": model.context_tokens, "maxOutputTokens": model.max_output_tokens,
        "description": model.description, "fit": fit, "fitLabel": _FIT_LABELS[fit],
        "installedId": _installed(model.name, models), "reason": reason,
    }


def _has_capacity(measured, nominal):
    # Available RAM as reported by the OS can fall just short of its nominal capacity.
    return measured is not None and measured >= nominal - (0.5 if nominal <= 8 else 1)


def _memory_fit(model, profile):
    ram, vram, accelerator = profile["ramGiB"], profile["vramGiB"], profile["accelerator"]
    if not ram:
        return "unknown", "Укажи объём памяти компьютера, чтобы оценить запас."
    if accelerator == "discrete":
        if vram is None:
            return "unknown", "Для оценки нужна выделенная видеопамять."
        host_ram = 8 if model.unified_gib <= 12 else 16
        fits = _has_capacity(ram, host_ram) and _has_capacity(vram, model.vram_gib)
        reason = f"Для размещения на видеокарте ориентир — {model.vram_gib} ГиБ видеопамяти и {host_ram} ГиБ ОЗУ."
    else:
        fits = _has_capacity(ram, model.unified_gib)
        kind = "общей памяти" if accelerator == "apple" else "ОЗУ"
        reason = f"Для короткого контекста ориентир — {model.unified_gib} ГиБ {kind}."
        if accelerator != "apple":
            reason += " Скорость на процессоре проверь на примере."
    return ("fits" if fits else "limited"), reason


def _recommend(profile, model_ids):
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
        return result, "unknown" if result["needsHardware"] else "unsupported"
    windows_x64 = system == "windows" and profile["arch"].lower() in ("x86_64", "amd64")
    if windows_x64 and profile["avx2"] is False:
        notes.append("Windows не подтверждает поддержку AVX2, нужную для LM Studio x64. Для этого ПК подойдут готовые карточки.")
        if model_ids:
            notes.append("Если у тебя уже работает другая локальная среда с моделью, проверь её на трёх вопросах.")
        return result, "unsupported"
    if ram is None:
        result["needsHardware"] = True
        notes.append("Для подбора модели укажи объём оперативной или объединённой памяти компьютера.")
        return result, "unknown"
    # Detected usable RAM may be a little below the nominal 8/16 GiB.
    if ram < 7.5:
        notes.append("При таком объёме памяти начни с готовых карточек без модели. Они оставят память браузеру и распознаванию.")
        return result, "limited"

    tier = "small"
    other_tier = "tiny"
    reason = "Компактная модель оставляет больше памяти системе, браузеру и распознаванию."
    other_reason = "Меньший файл для первого запуска. Проверь точность на вопросах из своего документа."
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
        other_tier = next((key for key in ("mixture", "qwen14b", "gemma12b", "large")
                           if _memory_fit(_MODELS[key], profile)[0] == "fits"), "small")
        other_reason = ("Попробуй меньшую модель, если ответы 4B приходят после таймера."
                        if other_tier == "small" else
                        "Есть запас памяти для более крупной модели. Сравни точность и время на тех же вопросах.")
    if windows_x64 and profile["avx2"] is None:
        notes.append("Для LM Studio в Windows x64 нужен процессор с AVX2.")
    notes.append("Начни с контекста 4096 токенов. Размер файла указан для скачивания. При работе дополнительную память занимают контекст и служебные буферы.")
    result["primary"] = _card(_MODELS[tier], "recommended", reason, model_ids)
    result["alternative"] = _card(_MODELS[other_tier], "alternative", other_reason, model_ids)
    result["installedMatch"] = result["primary"]["installedId"]
    if result["installedMatch"]:
        notes.append("Локальный сервер видит эту модель. Если в её имени не указано сжатие, проверь его в библиотеке моделей.")
    return result, None


def recommend(profile=None, model_ids=()):
    profile = _profile(detect_hardware() if profile is None else profile)
    result, unavailable_fit = _recommend(profile, model_ids)
    picks = {card["id"]: card for card in (result["primary"], result["alternative"]) if card}
    catalog = []
    for model in sorted(_MODELS.values(), key=lambda item: item.file_bytes):
        pick = picks.get(model.name.lower())
        if pick:
            catalog.append(pick)
            continue
        if unavailable_fit:
            fit, reason = unavailable_fit, result["notes"][0]
        else:
            fit, reason = _memory_fit(model, profile)
        catalog.append(_card(model, fit, reason, model_ids))
    result["catalog"] = catalog
    return result


if __name__ == "__main__" and sys.argv[1:] == ["--windows-gpu"] and os.name == "nt":
    try:
        print(json.dumps(_dxcore_gpu()))
    except (OSError, AttributeError):
        print("null")
