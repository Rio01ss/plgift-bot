import asyncio
import subprocess
import sys
from aiohttp import web

async def handle(request):
    return web.Response(text="PLGiftBot is running! ✅")

app = web.Application()
app.router.add_get('/', handle)

async def web_server():
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    print("🌐 Веб-сервер запущен на порту 8080 (для UptimeRobot)")

async def main():
    asyncio.create_task(web_server())

    proc1 = subprocess.Popen([sys.executable, "main_bot.py"])
    proc2 = subprocess.Popen([sys.executable, "admin_bot.py"])

    print("🚀 Оба бота запущены!")
    print("Основной бот: main_bot.py")
    print("Админ бот: admin_bot.py")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
