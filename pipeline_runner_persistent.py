#!/usr/bin/env python3
"""Persistent pipeline runner — keeps browser pool open across batches."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.browser_async import get_pool, shutdown_pool
from app.pipeline_async import run_pipeline_async


async def main():
    print("=" * 60)
    print("ORBITAL PIPELINE RUNNER (PERSISTENT)")
    print("Targets: hedge funds, family offices, crypto firms, VCs")
    print("=" * 60)

    # Initialize pool once and keep it warm
    print("[POOL] Warming up browser pool...")
    pool = await get_pool()
    print(f"[POOL] Ready with {pool.pool_size} contexts")

    wait_seconds = int(os.getenv("PIPELINE_INTERVAL", "60"))

    while True:
        print("\n[PIPELINE] Starting batch...")
        start = time.time()
        try:
            await run_pipeline_async()
        except Exception as e:
            print(f"[PIPELINE] Batch error: {e}")
        elapsed = time.time() - start
        print(f"[PIPELINE] Batch completed in {elapsed:.1f}s.")

        # Quick sleep before next batch
        sleep_time = max(5, wait_seconds - int(elapsed))
        print(f"[PIPELINE] Waiting {sleep_time}s...")
        await asyncio.sleep(sleep_time)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[PIPELINE] Interrupted. Shutting down...")
        asyncio.run(shutdown_pool())
