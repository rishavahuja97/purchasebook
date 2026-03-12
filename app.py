from flask import Flask, request, jsonify, render_template
from supabase import create_client, Client
import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.background import BackgroundScheduler
import os, json, logging
from datetime import datetime, timezone

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── Supabase ─────────────────────────────────────────────────────────────────
def get_db() -> Client:
    url = os.environ.get('SUPABASE_URL')
    key = os.environ.get('SUPABASE_KEY')
    if not url or not key:
        raise Exception("SUPABASE_URL and SUPABASE_KEY environment variables required")
    return create_client(url, key)

# ── Google Sheets backup ──────────────────────────────────────────────────────
GSHEETS_SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def get_gsheet():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id   = os.environ.get('SHEET_ID')
    if not creds_json or not sheet_id:
        raise Exception("GOOGLE_CREDENTIALS and SHEET_ID required for backup")
    creds  = Credentials.from_service_account_info(json.loads(creds_json), scopes=GSHEETS_SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)

def ensure_backup_sheets(book):
    existing = [ws.title for ws in book.worksheets()]
    headers = {
        'Purchases': ['ID','Date','Manufacturer','Pieces','Rate','Total','Notes','CreatedAt'],
        'Bills':     ['ID','WeekStart','WeekEnd','Manufacturer','Pieces','Amount','Notes','CreatedAt'],
        'Payments':  ['ID','Date','Manufacturer','Amount','Notes','CreatedAt'],
        'BackupLog': ['BackedUpAt','Purchases','Bills','Payments'],
    }
    for name, hdrs in headers.items():
        if name not in existing:
            ws = book.add_worksheet(name, rows=5000, cols=len(hdrs)+1)
            ws.append_row(hdrs)

def run_daily_backup():
    """Runs at midnight IST — writes full snapshot to Google Sheets."""
    log.info("Starting daily Google Sheets backup...")
    try:
        db   = get_db()
        book = get_gsheet()
        ensure_backup_sheets(book)

        purchases = db.table('purchases').select('*').order('created_at').execute().data
        bills     = db.table('bills').select('*').order('created_at').execute().data
        payments  = db.table('payments').select('*').order('created_at').execute().data

        now = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

        def overwrite_sheet(ws_name, rows, cols):
            ws = book.worksheet(ws_name)
            last_row = ws.row_count
            if last_row > 1:
                ws.delete_rows(2, last_row)
            if rows:
                ws.append_rows([[str(r.get(c,'')) for c in cols] for r in rows])

        overwrite_sheet('Purchases', purchases,
            ['id','date','manufacturer','pieces','rate','total','notes','created_at'])
        overwrite_sheet('Bills', bills,
            ['id','week_start','week_end','manufacturer','pieces','amount','notes','created_at'])
        overwrite_sheet('Payments', payments,
            ['id','date','manufacturer','amount','notes','created_at'])

        log_ws = book.worksheet('BackupLog')
        log_ws.append_row([now, len(purchases), len(bills), len(payments)])

        log.info(f"Backup complete — {len(purchases)} purchases, {len(bills)} bills, {len(payments)} payments")
    except Exception as e:
        log.error(f"Backup failed: {e}")

# ── Keep-alive ping (every 5 days) ───────────────────────────────────────────
def keep_alive():
    """Pings Supabase every 5 days so the free tier never goes inactive."""
    try:
        db = get_db()
        db.table('purchases').select('id').limit(1).execute()
        log.info("Keep-alive ping sent to Supabase ✅")
    except Exception as e:
        log.warning(f"Keep-alive ping failed: {e}")

# ── Scheduler (midnight IST = 18:30 UTC) ─────────────────────────────────────
scheduler = BackgroundScheduler(timezone='UTC')
scheduler.add_job(run_daily_backup, 'cron', hour=18, minute=30)
scheduler.add_job(keep_alive, 'interval', days=5)
scheduler.start()

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ping')
def ping():
    return 'ok', 200

@app.route('/api/load')
def load_all():
    try:
        db = get_db()
        purchases = db.table('purchases').select('*').order('date', desc=True).execute().data
        bills     = db.table('bills').select('*').order('created_at', desc=True).execute().data
        payments  = db.table('payments').select('*').order('date', desc=True).execute().data
        return jsonify({'purchases': purchases, 'bills': bills, 'payments': payments})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/purchases', methods=['POST'])
def add_purchase():
    try:
        d = request.json
        pieces = float(d['pieces'])
        rate   = float(d['rate'])
        total  = round(pieces * rate, 2)
        row = {
            'date':         d['date'],
            'manufacturer': d['manufacturer'].strip(),
            'pieces':       pieces,
            'rate':         rate,
            'total':        total,
            'notes':        d.get('notes','').strip(),
        }
        res = get_db().table('purchases').insert(row).execute()
        return jsonify({'ok': True, 'record': res.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/purchases/<int:pid>', methods=['DELETE'])
def delete_purchase(pid):
    try:
        get_db().table('purchases').delete().eq('id', pid).execute()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bills', methods=['POST'])
def save_bill():
    try:
        d = request.json
        row = {
            'week_start':   d['week_start'],
            'week_end':     d['week_end'],
            'manufacturer': d['manufacturer'],
            'pieces':       float(d['pieces']),
            'amount':       float(d['amount']),
            'notes':        d.get('notes',''),
        }
        res = get_db().table('bills').insert(row).execute()
        return jsonify({'ok': True, 'record': res.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/bills/<int:bid>', methods=['DELETE'])
def delete_bill(bid):
    try:
        get_db().table('bills').delete().eq('id', bid).execute()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/payments', methods=['POST'])
def add_payment():
    try:
        d = request.json
        row = {
            'date':         d['date'],
            'manufacturer': d['manufacturer'].strip(),
            'amount':       float(d['amount']),
            'notes':        d.get('notes','').strip(),
        }
        res = get_db().table('payments').insert(row).execute()
        return jsonify({'ok': True, 'record': res.data[0]})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/payments/<int:pid>', methods=['DELETE'])
def delete_payment(pid):
    try:
        get_db().table('payments').delete().eq('id', pid).execute()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/backup', methods=['POST'])
def manual_backup():
    try:
        run_daily_backup()
        return jsonify({'ok': True, 'message': 'Backup complete'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5051))
    print(f"\n✅ PurchaseBook running at: http://localhost:{port}\n")
    app.run(debug=False, host='0.0.0.0', port=port)
