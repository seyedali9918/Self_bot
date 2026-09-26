#!/bin/bash
python -c "import asyncio; asyncio.set_event_loop(asyncio.new_event_loop()); exec(open('bot.py').read())"
