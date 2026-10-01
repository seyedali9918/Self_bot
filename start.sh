#!/bin/bash

# ============================================
# اجرای FastAPI (مینی‌اپ)
# ============================================
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} &
MINIAPP_PID=$!

# ============================================
# اجرای ربات تلگرام
# ============================================
python bot.py &
BOT_PID=$!

echo "✅ Mini App started (PID: $MINIAPP_PID)"
echo "✅ Bot started (PID: $BOT_PID)"

# ============================================
# اگه یکی از پروسه‌ها بمیره، بقیه رو هم بکش
# ============================================
trap "kill $MINIAPP_PID $BOT_PID 2>/dev/null; exit" SIGTERM SIGINT

# حلقه‌ای که وضعیت رو چک می‌کنه
while true; do
    if ! kill -0 $MINIAPP_PID 2>/dev/null; then
        echo "❌ Mini App died, restarting..."
        break
    fi
    if ! kill -0 $BOT_PID 2>/dev/null; then
        echo "❌ Bot died, restarting..."
        break
    fi
    sleep 5
done

# اگه یکی از پروسه‌ها مرد، بقیه رو بکش
kill $MINIAPP_PID $BOT_PID 2>/dev/null
wait
