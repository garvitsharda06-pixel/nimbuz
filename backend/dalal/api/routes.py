"""Extra API routes registered by later phases (board, journal, research, notes)."""

from __future__ import annotations

from fastapi import FastAPI

from dalal.live.runtime import Runtime


def register(app: FastAPI, rt: Runtime) -> None:  # filled in by later phases
    return None
