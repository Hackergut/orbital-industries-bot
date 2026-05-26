#!/usr/bin/env python3
"""Standalone pipeline runner — one batch per invocation."""
import asyncio
import os
import sys

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.browser_async import shutdown_pool
from app.pipeline_async import run_pipeline_async


async def main():
    print("=" * 60)
    print("ORBITAL PIPELINE RUNNER")
    print("Targets: hedge funds, family offices, crypto firms, VCs")
    print("=" * 60)
    
    print("\n[PIPELINE] Starting batch...")
    try:
        await run_pipeline_async()
        print("[PIPELINE] Batch completed.")
    finally:
        print("[PIPELINE] Shutting down browser pool...")
        try:
            await shutdown_pool()
            print("[PIPELINE] Browser pool shut down.")
        except Exception as e:
            print(f"[PIPELINE] Shutdown warning: {e}")


if __name__ == "__main__":
    asyncio.run(main())
