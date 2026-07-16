from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import date, timedelta
from sqlalchemy import text
from loguru import logger
from modules.asset_engine import AssetEngine
from modules.contract_engine import ContractEngine
from modules.notification_engine import NotificationEngine
from core.database import SessionLocal
from config.settings import settings

def run_monthly_auto_posting():
    """
    Job scheduler yang berjalan otomatis untuk memproses depresiasi 
    dan amortisasi semua entity aktif.
    """
    db = SessionLocal()
    try:
        # Target period adalah bulan sebelumnya.
        # Jika dijalankan tanggal 1, maka 'yesterday' adalah tanggal terakhir bulan lalu.
        target_date = date.today() - timedelta(days=1)
        
        # Ambil semua entity yang aktif
        entities = db.execute(text("SELECT id, name FROM entity WHERE is_active = TRUE")).fetchall()
        
        engine = AssetEngine(db)
        for entity in entities:
            logger.info(f"Running auto-posting for entity: {entity.name} ({entity.id})")
            
            # 1. Post Depresiasi Aset Tetap
            dep_res = engine.post_monthly_depreciation(entity.id, target_date, posted_by="system-scheduler")
            logger.info(f"Entity {entity.name}: {dep_res.get('posted', 0)} depreciation journals posted.")
            
            # 2. Post Amortisasi Biaya Dibayar Dimuka
            am_res = engine.post_monthly_amortization(entity.id, target_date, posted_by="system-scheduler")
            logger.info(f"Entity {entity.name}: {am_res.get('posted', 0)} amortization journals posted.")
            
    except Exception as e:
        logger.error(f"Error in monthly auto-posting task: {e}")
    finally:
        db.close()

def run_daily_contract_aging():
    """
    Logika A — update invoice OVERDUE setelah lewat TOP kontrak.
    Dijalankan setiap hari jam 00:05.
    """
    db = SessionLocal()
    try:
        result = ContractEngine.run_invoice_aging(db)
        logger.info(
            f"Contract aging: {result['invoices_set_overdue']} → OVERDUE, "
            f"{result['due_dates_synced']} due_date synced"
        )
    except Exception as e:
        logger.error(f"Error in contract aging task: {e}")
    finally:
        db.close()

def run_daily_notifications():
    """Job untuk mengecek overdue, stok, dan kontrak setiap hari."""
    db = SessionLocal()
    try:
        engine = NotificationEngine(db)
        engine.run_all_checks()
        logger.info("Daily notification checks completed.")
    except Exception as e:
        logger.error(f"Error in notification task: {e}")
    finally:
        db.close()


def start_scheduler():
    """
    Inisialisasi BackgroundScheduler dengan APScheduler.
    """
    scheduler = BackgroundScheduler(timezone=settings.SCHEDULER_TIMEZONE)
    
    # Ambil hari eksekusi dari settings (default ke tanggal 1)
    run_day = settings.DEPRECIATION_RUN_DAY or 1
    
    trigger = CronTrigger(day=run_day, hour=1, minute=0)
    scheduler.add_job(
        run_monthly_auto_posting, 
        trigger, 
        id="monthly_depreciation_amortization",
        replace_existing=True
    )
    
    # Logika A: daily contract invoice aging — setiap hari 00:05
    scheduler.add_job(
        run_daily_contract_aging,
        CronTrigger(hour=0, minute=5),
        id="daily_contract_aging",
        replace_existing=True,
    )

    # Logika C: Scan Notifikasi — setiap hari jam 06:00 pagi
    scheduler.add_job(
        run_daily_notifications,
        CronTrigger(hour=6, minute=0),
        id="daily_notifications",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(f"APScheduler started. Monthly task on day {run_day} 01:00 | Daily aging at 00:05.")
    return scheduler