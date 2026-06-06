"""Small helpers for surfacing nested runtime failures.

The orchestrator often catches exceptions at async boundaries where Python
3.11 wraps the real cause in an ExceptionGroup. Plain ``str(exc)`` hides the
sub-exceptions, so keep formatting in one place and make the stored node error
useful enough to debug from replay alone.
"""

from __future__ import annotations

import traceback


def format_exception(exc: BaseException, *, include_traceback: bool = True) -> str:
    """Return a compact but cause-preserving exception description."""
    lines: list[str] = [f"{type(exc).__name__}: {exc}"]
    if isinstance(exc, BaseExceptionGroup):
        lines.append("sub-exceptions:")
        for i, sub in enumerate(exc.exceptions, start=1):
            lines.extend(_format_group_member(i, sub, indent="  "))
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        cause_first = format_exception(cause, include_traceback=False).splitlines()[0]
        lines.append(f"caused by: {cause_first}")
    if include_traceback:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
        if tb:
            lines.append("traceback:")
            lines.append(tb)
    return "\n".join(lines)


def _format_group_member(index: int, exc: BaseException, *, indent: str) -> list[str]:
    lines = [f"{indent}{index}. {type(exc).__name__}: {exc}"]
    if isinstance(exc, BaseExceptionGroup):
        for i, sub in enumerate(exc.exceptions, start=1):
            lines.extend(_format_group_member(i, sub, indent=indent + "  "))
    return lines
