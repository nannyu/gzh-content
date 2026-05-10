"""
公众号爆款文章 Web 应用
功能：搜索、查看、筛选、定时抓取、数据库存储
"""

import os
import sqlite3
import json
import time
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, g

from scripts.fetch_gzh_trends import fetch_gzh_trends, parse_count

app = Flask(__name__)
app.config['DATABASE'] = os.path.join(os.path.dirname(__file__), 'gzh_data.db')

# ============ 数据库 ============

def get_db():
    """获取数据库连接（每个请求一个）"""
    if 'db' not in g:
        g.db = sqlite3.connect(app.config['DATABASE'])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    """初始化数据库表"""
    db = sqlite3.connect(app.config['DATABASE'])
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript('''
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id TEXT UNIQUE NOT NULL,
            title TEXT,
            summary TEXT,
            account_id TEXT,
            account_name TEXT,
            fans TEXT,
            public_time TEXT,
            ori_url TEXT,
            cover_url TEXT,
            like_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            share_count INTEGER DEFAULT 0,
            interactive_count INTEGER DEFAULT 0,
            clicks_count TEXT,
            watch_count TEXT,
            original_flag INTEGER DEFAULT 0,
            order_num INTEGER DEFAULT 0,
            category TEXT,
            keyword TEXT,
            data_score REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_articles_keyword ON articles(keyword);
        CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category);
        CREATE INDEX IF NOT EXISTS idx_articles_public_time ON articles(public_time);
        CREATE INDEX IF NOT EXISTS idx_articles_clicks ON articles(clicks_count);

        CREATE TABLE IF NOT EXISTS fetch_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT,
            total_fetched INTEGER DEFAULT 0,
            new_inserted INTEGER DEFAULT 0,
            updated INTEGER DEFAULT 0,
            status TEXT DEFAULT 'success',
            error_msg TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE TABLE IF NOT EXISTS scheduled_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT UNIQUE NOT NULL,
            is_active INTEGER DEFAULT 1,
            cron_hour INTEGER DEFAULT 19,
            cron_minute INTEGER DEFAULT 0,
            last_fetched_at TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        );
    ''')
    db.commit()
    db.close()


# ============ 数据存储 ============

def save_articles_to_db(data, keyword):
    """将抓取的文章数据存入数据库"""
    db = sqlite3.connect(app.config['DATABASE'])
    db.execute("PRAGMA journal_mode=WAL")

    categories = [
        ('low_fan_explosive', '低粉高阅读'),
        ('ten_w_reading', '阅读靠前'),
        ('original_rank', '原创靠前'),
        ('one_w_reading', '数据增长中'),
    ]

    new_count = 0
    update_count = 0

    for cat_key, cat_name in categories:
        items = data.get(cat_key, [])
        for item in items:
            photo_id = item.get('photoId', '')
            if not photo_id:
                continue

            title = item.get('title', '') or (item.get('summary', '') or '')[:50]
            summary = item.get('summary', '')
            account_id = item.get('accountId', '')
            account_name = item.get('userName', '') or account_id
            fans = str(item.get('fans', ''))
            public_time = item.get('publicTime', '')
            ori_url = item.get('oriUrl', '')
            cover_url = item.get('coverUrl', '')
            like_count = parse_count(item.get('likeCount', 0))
            comment_count = parse_count(item.get('commentCount', 0))
            share_count = parse_count(item.get('shareCount', 0))
            interactive_count = parse_count(item.get('interactiveCount', 0))
            clicks_count = str(item.get('clicksCount', '0'))
            watch_count = str(item.get('watchCount', '0'))
            original_flag = item.get('originalFlag', 0)
            order_num = item.get('orderNum', 0)

            # UPSERT
            existing = db.execute(
                "SELECT id FROM articles WHERE photo_id = ?", (photo_id,)
            ).fetchone()

            if existing:
                db.execute('''
                    UPDATE articles SET
                        like_count = ?, comment_count = ?, share_count = ?,
                        interactive_count = ?, clicks_count = ?, watch_count = ?,
                        updated_at = datetime('now', 'localtime')
                    WHERE photo_id = ?
                ''', (like_count, comment_count, share_count,
                      interactive_count, clicks_count, watch_count, photo_id))
                update_count += 1
            else:
                db.execute('''
                    INSERT INTO articles (
                        photo_id, title, summary, account_id, account_name,
                        fans, public_time, ori_url, cover_url,
                        like_count, comment_count, share_count,
                        interactive_count, clicks_count, watch_count,
                        original_flag, order_num, category, keyword
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (photo_id, title, summary, account_id, account_name,
                      fans, public_time, ori_url, cover_url,
                      like_count, comment_count, share_count,
                      interactive_count, clicks_count, watch_count,
                      original_flag, order_num, cat_name, keyword))
                new_count += 1

    db.commit()

    # 记录抓取日志
    total = sum(len(data.get(k, [])) for k in ['low_fan_explosive', 'ten_w_reading', 'original_rank', 'one_w_reading'])
    db.execute('''
        INSERT INTO fetch_logs (keyword, total_fetched, new_inserted, updated, status)
        VALUES (?, ?, ?, ?, 'success')
    ''', (keyword, total, new_count, update_count))
    db.commit()
    db.close()

    return {'total': total, 'new': new_count, 'updated': update_count}


# ============ 定时任务 ============

def scheduled_fetch():
    """定时抓取任务"""
    with app.app_context():
        db = sqlite3.connect(app.config['DATABASE'])
        db.row_factory = sqlite3.Row
        keywords = db.execute(
            "SELECT keyword FROM scheduled_keywords WHERE is_active = 1"
        ).fetchall()
        db.close()

        if not keywords:
            keywords = [{'keyword': ''}]  # 默认抓取全站

        for row in keywords:
            kw = row['keyword'] if isinstance(row, dict) else row[0]
            try:
                start_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
                data = fetch_gzh_trends(kw, start_date=start_date)
                save_articles_to_db(data, kw)
                print(f"[定时任务] 抓取成功: keyword='{kw}'")
            except Exception as e:
                print(f"[定时任务] 抓取失败: keyword='{kw}', error={e}")
                # 记录失败日志
                db = sqlite3.connect(app.config['DATABASE'])
                db.execute('''
                    INSERT INTO fetch_logs (keyword, status, error_msg)
                    VALUES (?, 'failed', ?)
                ''', (kw, str(e)))
                db.commit()
                db.close()


def start_scheduler():
    """启动定时任务调度器"""
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()
    # 每天 19:00 执行抓取
    scheduler.add_job(scheduled_fetch, 'cron', hour=19, minute=0, id='daily_fetch')
    # 每 6 小时也执行一次，保持数据新鲜
    scheduler.add_job(scheduled_fetch, 'interval', hours=6, id='interval_fetch')
    scheduler.start()
    print("[调度器] 定时任务已启动：每天19:00 + 每6小时")


# ============ 路由：页面 ============

@app.route('/')
def index():
    """首页"""
    return render_template('index.html')


# ============ 路由：API ============

@app.route('/api/search', methods=['GET'])
def api_search():
    """搜索文章（从数据库查询，如果无结果则实时抓取）"""
    keyword = request.args.get('keyword', '').strip()
    category = request.args.get('category', '')
    sort_by = request.args.get('sort', 'public_time')
    order = request.args.get('order', 'desc')
    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 20))
    days = int(request.args.get('days', 7))

    db = get_db()

    # 构建查询
    conditions = []
    params = []

    # 时间范围
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    conditions.append("public_time >= ?")
    params.append(start_date)

    # 关键词搜索
    if keyword:
        conditions.append("(title LIKE ? OR summary LIKE ? OR keyword LIKE ?)")
        like_kw = f"%{keyword}%"
        params.extend([like_kw, like_kw, like_kw])

    # 分类筛选
    if category:
        conditions.append("category = ?")
        params.append(category)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # 排序
    valid_sorts = {
        'public_time': 'public_time',
        'interactive_count': 'interactive_count',
        'like_count': 'like_count',
        'share_count': 'share_count',
        'comment_count': 'comment_count',
    }
    sort_col = valid_sorts.get(sort_by, 'public_time')
    order_dir = 'DESC' if order == 'desc' else 'ASC'

    # 总数
    total = db.execute(
        f"SELECT COUNT(*) FROM articles WHERE {where_clause}", params
    ).fetchone()[0]

    # 分页查询
    offset = (page - 1) * page_size
    rows = db.execute(
        f"""SELECT * FROM articles WHERE {where_clause}
            ORDER BY {sort_col} {order_dir}
            LIMIT ? OFFSET ?""",
        params + [page_size, offset]
    ).fetchall()

    articles = [dict(row) for row in rows]

    return jsonify({
        'total': total,
        'page': page,
        'page_size': page_size,
        'pages': (total + page_size - 1) // page_size,
        'articles': articles
    })


@app.route('/api/fetch', methods=['POST'])
def api_fetch():
    """手动触发抓取"""
    data = request.get_json() or {}
    keyword = data.get('keyword', '').strip()
    days = int(data.get('days', 7))

    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    try:
        result = fetch_gzh_trends(keyword, start_date=start_date)
        stats = save_articles_to_db(result, keyword)
        return jsonify({
            'success': True,
            'message': f"抓取完成：共{stats['total']}条，新增{stats['new']}条，更新{stats['updated']}条",
            'stats': stats
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f"抓取失败：{str(e)}"}), 500


@app.route('/api/article/<photo_id>')
def api_article_detail(photo_id):
    """文章详情"""
    db = get_db()
    row = db.execute("SELECT * FROM articles WHERE photo_id = ?", (photo_id,)).fetchone()
    if row:
        return jsonify(dict(row))
    return jsonify({'error': '文章不存在'}), 404


@app.route('/api/stats')
def api_stats():
    """统计信息"""
    db = get_db()
    total_articles = db.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    total_keywords = db.execute("SELECT COUNT(DISTINCT keyword) FROM articles").fetchone()[0]
    categories = db.execute(
        "SELECT category, COUNT(*) as count FROM articles GROUP BY category"
    ).fetchall()
    recent_logs = db.execute(
        "SELECT * FROM fetch_logs ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    return jsonify({
        'total_articles': total_articles,
        'total_keywords': total_keywords,
        'categories': [dict(r) for r in categories],
        'recent_logs': [dict(r) for r in recent_logs]
    })


@app.route('/api/keywords', methods=['GET'])
def api_keywords():
    """获取定时抓取关键词列表"""
    db = get_db()
    rows = db.execute("SELECT * FROM scheduled_keywords ORDER BY created_at DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/keywords', methods=['POST'])
def api_add_keyword():
    """添加定时抓取关键词"""
    data = request.get_json() or {}
    keyword = data.get('keyword', '').strip()

    db = get_db()
    try:
        db.execute(
            "INSERT OR IGNORE INTO scheduled_keywords (keyword) VALUES (?)",
            (keyword,)
        )
        db.commit()
        return jsonify({'success': True, 'message': f"已添加关键词：{keyword or '全站热门'}"})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 400


@app.route('/api/keywords/<int:kid>', methods=['DELETE'])
def api_delete_keyword(kid):
    """删除定时抓取关键词"""
    db = get_db()
    db.execute("DELETE FROM scheduled_keywords WHERE id = ?", (kid,))
    db.commit()
    return jsonify({'success': True})


@app.route('/api/keywords/<int:kid>/toggle', methods=['POST'])
def api_toggle_keyword(kid):
    """启用/禁用关键词"""
    db = get_db()
    db.execute(
        "UPDATE scheduled_keywords SET is_active = CASE WHEN is_active=1 THEN 0 ELSE 1 END WHERE id = ?",
        (kid,)
    )
    db.commit()
    return jsonify({'success': True})


# ============ 启动 ============

if __name__ == '__main__':
    init_db()
    start_scheduler()
    print("\n🚀 公众号爆款文章 Web 应用已启动")
    print("📍 访问地址: http://127.0.0.1:5000")
    print("📊 数据库: gzh_data.db")
    print("⏰ 定时任务: 每天19:00 + 每6小时\n")
    app.run(host='0.0.0.0', port=5000, debug=False)
