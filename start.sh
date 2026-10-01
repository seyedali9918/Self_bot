#!/bin/bash

# ============================================
# اجرای FastAPI (مینی‌اپ) و هدایت لاگ
# ============================================
echo "🚀 Starting Mini App..."
uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} 2>&1 &
MINIAPP_PID=$!

sleep 2

# ============================================
# اجرای ربات و هدایت لاگ به خروجی Railway
# ============================================
echo "🚀 Starting Bot..."
python -u bot.py 2>&1 &
BOT_PID=$!

echo "✅ Mini App PID: $MINIAPP_PID"
echo "✅ Bot PID: $BOT_PID"

# ============================================
# مانیتور پروسه‌ها
# ============================================
while true; do
    if ! kill -0 $MINIAPP_PID 2>/dev/null; then
        echo "❌ Mini App died!"
        break
    fi
    if ! kill -0 $BOT_PID 2>/dev/null; then
        echo "❌ Bot died!"
        break
    fi
    sleep 5
done

kill $MINIAPP_PID $BOT_PID 2>/dev/null
wait
