"""
Подключение роутеров админ-панели.
"""
from aiogram import Router

from bot.handlers.admin.main import router as main_router
from bot.handlers.admin.servers import router as servers_router
from bot.handlers.admin.payments import router as payments_router
from bot.handlers.admin.tariffs import router as tariffs_router
from bot.handlers.admin.broadcast import router as broadcast_router
from bot.handlers.admin.users import router as users_router
from bot.handlers.admin.system import router as system_router
from bot.handlers.admin.trial import router as trial_router
from bot.handlers.admin.referral import router as referral_router

admin_router = Router()

admin_router.include_router(main_router)
admin_router.include_router(servers_router)
admin_router.include_router(payments_router)
admin_router.include_router(tariffs_router)
admin_router.include_router(broadcast_router)
admin_router.include_router(users_router)
admin_router.include_router(system_router)
admin_router.include_router(trial_router)
admin_router.include_router(referral_router)

