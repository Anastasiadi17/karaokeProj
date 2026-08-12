def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Разобрать заголовок Range. Возвращает (start, end) включительно.

    None — заголовка нет или единица измерения не bytes (отдаём файл целиком).
    ValueError — диапазон синтаксически верен, но недостижим: ответ 416.
    """
    if not header:
        return None

    header = header.strip()
    if not header.startswith("bytes="):
        return None

    spec = header[len("bytes="):].split(",")[0].strip()
    start_raw, _, end_raw = spec.partition("-")

    if not start_raw:
        if not end_raw:
            raise ValueError(f"пустой диапазон: {header!r}")
        length = int(end_raw)
        if length <= 0:
            raise ValueError(f"недопустимая длина суффикса: {header!r}")
        start = max(0, size - length)
        return start, size - 1

    start = int(start_raw)
    if start >= size:
        raise ValueError(f"начало за пределами файла: {header!r}")

    end = int(end_raw) if end_raw else size - 1
    end = min(end, size - 1)
    if end < start:
        raise ValueError(f"конец раньше начала: {header!r}")

    return start, end
