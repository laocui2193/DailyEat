# -*- coding: utf-8 -*-
"""随心吃吃 - 后端服务（Python 版）
对应 JS 版的 server/server.js，接口完全一致，前端零改动。
启动：python app.py（默认端口 3000）
依赖：flask（pip install flask）
"""
import hashlib
import json
import secrets
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, request, jsonify, Response, stream_with_context

import db
import rag

# Windows 控制台输出中文避免 GBK 报错
sys.stdout.reconfigure(encoding='utf-8')

app = Flask(__name__)
PORT = 3000


# ==================== CORS ====================
# Electron 页面来自 file://，跨域请求必须放行
@app.after_request
def add_cors_headers(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    return resp


# ==================== 密码安全 ====================
def hash_password(password: str, salt: str) -> str:
    """pbkdf2 加盐哈希（Python 内置 hashlib，无需安装）"""
    return hashlib.pbkdf2_hmac('sha512', password.encode(), salt.encode(), 10000, dklen=64).hex()


# ==================== 认证装饰器 ====================
def require_auth(f):
    """从 Authorization: Bearer <token> 解析用户，失败返回 401"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        header = request.headers.get('Authorization', '')
        token = header[7:] if header.startswith('Bearer ') else None
        user = db.find('users', lambda u: u.get('token') == token) if token else None
        if not user:
            return jsonify(error='未登录或登录已过期'), 401
        return f(user, *args, **kwargs)
    return wrapper


# ==================== 用户系统 ====================
@app.post('/api/register')
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    if not username or not password:
        return jsonify(error='用户名和密码不能为空'), 400
    if not (2 <= len(username) <= 20):
        return jsonify(error='用户名需 2-20 个字符'), 400
    if len(password) < 6:
        return jsonify(error='密码至少 6 位'), 400
    if db.find('users', lambda u: u['username'] == username):
        return jsonify(error='用户名已被注册'), 409

    salt = secrets.token_hex(16)
    user = db.insert('users', {
        'username': username,
        'salt': salt,
        'hash': hash_password(password, salt),
        'token': secrets.token_hex(32),
        'favorites': [],
    })
    # 返回给客户端的不含 salt/hash
    return jsonify(token=user['token'], username=user['username'], favorites=user['favorites'])


@app.post('/api/login')
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''

    user = db.find('users', lambda u: u['username'] == username)
    if not user or hash_password(password, user['salt']) != user['hash']:
        return jsonify(error='用户名或密码错误'), 401

    user['token'] = secrets.token_hex(32)  # 每次登录换新 token
    db.update('users', user['id'], {'token': user['token']})
    return jsonify(token=user['token'], username=user['username'], favorites=user['favorites'])


@app.get('/api/me')
@require_auth
def me(user):
    return jsonify(username=user['username'], favorites=user['favorites'])


@app.get('/api/favorites')
@require_auth
def get_favorites(user):
    return jsonify(favorites=user['favorites'])


@app.put('/api/favorites')
@require_auth
def put_favorites(user):
    ids = (request.get_json(silent=True) or {}).get('ids')
    if not isinstance(ids, list):
        return jsonify(error='ids 必须是数组'), 400
    db.update('users', user['id'], {'favorites': ids})
    return jsonify(favorites=ids)


# ==================== 评分评论 ====================
@app.post('/api/reviews')
@require_auth
def post_review(user):
    data = request.get_json(silent=True) or {}
    restaurant_id = data.get('restaurantId')
    rating = data.get('rating')
    comment = data.get('comment') or ''

    if not isinstance(restaurant_id, int) or restaurant_id < 1:
        return jsonify(error='餐厅 id 无效'), 400
    if not isinstance(rating, int) or not (1 <= rating <= 5):
        return jsonify(error='评分须为 1-5 的整数'), 400
    if len(comment) > 200:
        return jsonify(error='评论最多 200 字'), 400

    # 一用户一店一条：已评过就是改分
    existing = db.find('reviews', lambda r: r['userId'] == user['id'] and r['restaurantId'] == restaurant_id)
    if existing:
        review = db.update('reviews', existing['id'], {'rating': rating, 'comment': comment})
    else:
        review = db.insert('reviews', {
            'userId': user['id'],
            'username': user['username'],
            'restaurantId': restaurant_id,
            'rating': rating,
            'comment': comment,
        })
    return jsonify(review)


@app.get('/api/reviews')
def get_reviews():
    restaurant_id = request.args.get('restaurant_id', type=int)
    if not restaurant_id:
        return jsonify(error='缺少 restaurant_id 参数'), 400
    reviews = db.filter_rows('reviews', lambda r: r['restaurantId'] == restaurant_id)
    reviews.sort(key=lambda r: r['createdAt'], reverse=True)  # 新 → 旧
    avg = round(sum(r['rating'] for r in reviews) / len(reviews), 1) if reviews else None
    return jsonify(avg=avg, count=len(reviews), reviews=reviews)


# ==================== 打卡签到 ====================
@app.post('/api/checkins')
@require_auth
def post_checkin(user):
    data = request.get_json(silent=True) or {}
    restaurant_id = data.get('restaurantId')
    if not isinstance(restaurant_id, int) or restaurant_id < 1:
        return jsonify(error='餐厅 id 无效'), 400

    # 同一天重复打卡同一家 → 报错
    today = datetime.now().strftime('%Y-%m-%d')
    dup = db.find('checkins', lambda c: c['userId'] == user['id']
                   and c['restaurantId'] == restaurant_id
                   and c['createdAt'].startswith(today))
    if dup:
        return jsonify(error='今天已经在这家打过卡啦'), 409

    checkin = db.insert('checkins', {
        'userId': user['id'],
        'username': user['username'],
        'restaurantId': restaurant_id,
    })
    return jsonify(checkin=checkin, streak=calc_streak(user['id']), total=checkin_count(user['id']))


@app.get('/api/checkins/mine')
@require_auth
def my_checkins(user):
    return jsonify(streak=calc_streak(user['id']), total=checkin_count(user['id']))


def checkin_count(user_id):
    return len(db.filter_rows('checkins', lambda c: c['userId'] == user_id))


def calc_streak(user_id):
    """连续打卡天数：从今天（或昨天，若今天还没打）往前数连续的自然日"""
    days = set()
    for c in db.filter_rows('checkins', lambda c: c['userId'] == user_id):
        days.add(c['createdAt'][:10])
    if not days:
        return 0
    streak = 0
    d = datetime.now().date()
    if d.isoformat() not in days:
        d -= timedelta(days=1)  # 今天还没打 → 从昨天开始算
    while d.isoformat() in days:
        streak += 1
        d -= timedelta(days=1)
    return streak


# ==================== 我的评分（口味画像用） ====================
@app.get('/api/reviews/mine')
@require_auth
def my_reviews(user):
    reviews = db.filter_rows('reviews', lambda r: r['userId'] == user['id'])
    return jsonify(reviews=reviews)


# ==================== 长期偏好（蒸馏记忆） ====================
@app.get('/api/preferences/mine')
@require_auth
def my_preferences(user):
    return jsonify(preferences=load_user_preferences(user['id']))


# ==================== UGC 提交餐厅 ====================
@app.post('/api/submissions')
@require_auth
def post_submission(user):
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    cuisine = (data.get('cuisine') or '').strip()
    tag = (data.get('tag') or '').strip()
    price = (data.get('price') or '').strip()
    dist = (data.get('dist') or '').strip()
    addr = (data.get('addr') or '').strip()
    desc = (data.get('desc') or '').strip()
    dishes = data.get('dishes') or []

    if not all([name, cuisine, price, addr]):
        return jsonify(error='店名、菜系、人均、地址为必填'), 400
    if len(desc) > 300:
        return jsonify(error='简介最多 300 字'), 400
    if not isinstance(dishes, list) or len(dishes) > 5:
        return jsonify(error='推荐菜最多 5 个'), 400

    sub = db.insert('submissions', {
        'userId': user['id'],
        'username': user['username'],
        'name': name, 'cuisine': cuisine, 'tag': tag,
        'price': price, 'dist': dist, 'addr': addr,
        'desc': desc, 'dishes': [d.strip() for d in dishes if str(d).strip()],
        'status': 'pending',
    })
    return jsonify(submission=sub, message='已提交，等待审核')


@app.get('/api/submissions/mine')
@require_auth
def my_submissions(user):
    subs = db.filter_rows('submissions', lambda s: s['userId'] == user['id'])
    subs.sort(key=lambda s: s['createdAt'], reverse=True)
    return jsonify(submissions=subs)


@app.get('/api/submissions/approved')
def approved_submissions():
    """已审核通过的 UGC 餐厅，前端启动时并入推荐池"""
    subs = db.filter_rows('submissions', lambda s: s['status'] == 'approved')
    return jsonify(submissions=subs)


def load_admin_key():
    cfg = load_config()
    return cfg.get('admin_key', 'admin123')


@app.put('/api/submissions/<int:sub_id>/approve')
def approve_submission(sub_id):
    """审核通过（需 X-Admin-Key 请求头）"""
    if request.headers.get('X-Admin-Key') != load_admin_key():
        return jsonify(error='无审核权限'), 403
    sub = db.update('submissions', sub_id, {'status': 'approved'})
    if not sub:
        return jsonify(error='提交不存在'), 404
    return jsonify(submission=sub)


# ==================== AI Agent（Tool Calling + ReAct + 记忆） ====================
def load_config():
    cfg_path = Path(__file__).parent / 'config.json'
    if cfg_path.exists():
        return json.loads(cfg_path.read_text(encoding='utf-8'))
    return {}


def _parse_price(s):
    return int(''.join(ch for ch in str(s) if ch.isdigit()) or 0)


def _parse_dist(s):
    return int(''.join(ch for ch in str(s) if ch.isdigit()) or 0)


def load_restaurants():
    rdata = json.loads((Path(__file__).parent.parent / 'restaurants.json').read_text(encoding='utf-8'))
    return [r for r in rdata['restaurants'] if not r.get('closed')]


# ---------- 工具定义（OpenAI Function Calling 格式） ----------
AGENT_TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'search_restaurants',
            'description': '按条件筛选餐厅。返回餐厅列表（含 id/名称/菜系/人均/距离/米其林星/简介摘要）',
            'parameters': {
                'type': 'object',
                'properties': {
                    'major': {'type': 'string', 'description': '大类筛选，如 日本料理/火锅煲类/西餐欧陆/简餐茶餐'},
                    'sub': {'type': 'string', 'description': '子类筛选，如 拉面/寿喜烧/法国菜/煲仔饭/南美融合/日西融合/中西融合。融合类需求必须用此参数精确匹配'},
                    'max_price': {'type': 'integer', 'description': '人均上限（港币）'},
                    'min_price': {'type': 'integer', 'description': '人均下限（港币）'},
                    'max_dist': {'type': 'integer', 'description': '距离上限（米）'},
                    'no_booking': {'type': 'boolean', 'description': 'true = 只要无需预约的餐厅'},
                    'michelin': {'type': 'integer', 'description': '只要米其林星数 ≥ 此值的餐厅'},
                    'green': {'type': 'boolean', 'description': 'true = 只要米其林绿星餐厅（可持续餐饮）'},
                    'feature': {'type': 'string', 'description': '场景特征，候选：海景/夜景/户外露台/约会氛围/安静/亲子友好/排队热门/快食/深夜营业/商务宴请/名人打卡/高层视野/河景/浪漫/性价比高/老字号。用户说「有海景」「要安静」「适合约会」时优先用这个参数而不是 keyword'},
                    'has_promo': {'type': 'boolean', 'description': 'true = 只找有优惠活动的餐厅（如香港餐厅周）'},
                    'keyword': {'type': 'string', 'description': '关键词，匹配店名/菜系/简介（如 烧鹅/海胆/船面）'}
                }
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_restaurant_detail',
            'description': '查询某家餐厅的完整详情与真实食客评价',
            'parameters': {
                'type': 'object',
                'properties': {'id': {'type': 'integer', 'description': '餐厅 id'}},
                'required': ['id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_leaderboard',
            'description': '查看食客评分排行榜 TOP10',
            'parameters': {'type': 'object', 'properties': {}}
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'get_user_profile',
            'description': '查看当前用户的饮食偏好画像（评分历史/收藏/打卡）。用户说「根据我的口味」时调用',
            'parameters': {'type': 'object', 'properties': {}}
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'add_favorite',
            'description': '把餐厅加入用户收藏。只在用户明确要求「收藏/记下/加入收藏」这家店时调用（需要用户确认后执行）',
            'parameters': {
                'type': 'object',
                'properties': {'id': {'type': 'integer', 'description': '餐厅 id'}},
                'required': ['id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'checkin_restaurant',
            'description': '为当前用户打卡某家餐厅。只在用户明确要求「打卡/我吃了这家」时调用（需要用户确认后执行）',
            'parameters': {
                'type': 'object',
                'properties': {'id': {'type': 'integer', 'description': '餐厅 id'}},
                'required': ['id']
            }
        }
    },
    {
        'type': 'function',
        'function': {
            'name': 'similar_restaurants',
            'description': '语义相似搜索（RAG）：找「风格/口味相似」的餐厅。用户问「像XX那样的店」「有没有类似九记牛腩的清汤腩」「类似风格的小酒馆」时调用。比关键词搜索更强：语义相近即可命中',
            'parameters': {
                'type': 'object',
                'properties': {
                    'restaurant_id': {'type': 'integer', 'description': '以某家餐厅为参照找相似店'},
                    'query': {'type': 'string', 'description': '或直接用描述文字，如 清汤牛腩/浪漫海景法餐/怀旧冰室'}
                }
            }
        }
    }
]

# 写操作工具（需用户确认后才真正执行）
WRITE_TOOLS = {'add_favorite', 'checkin_restaurant'}

# 会话存储：写操作暂停时保存 Agent 中间状态，确认后恢复（内存版，10 分钟过期）
agent_sessions = {}

# 查询缓存：单条消息的重复提问 10 分钟内直接复用结果
agent_cache = {}


def _compact_restaurant(r):
    promo = r.get('promo')
    return {
        'id': r['id'], 'name': r['name'], 'cuisine': r['cuisine'],
        'price': r['price'], 'dist': r['dist'],
        'michelin': r.get('michelin'), 'book': bool(r.get('book')),
        'features': r.get('features', []),
        'promo': (promo or {}).get('title', '') if promo else '',
        'desc': (r.get('desc') or '')[:40]
    }


def execute_tool(name, args, user_id, restaurants):
    """执行工具并返回给 LLM 的结果（尽量精简，省 token）
    写操作工具不在此执行，返回 pending 标记，由 Agent 循环暂停并请求用户确认
    """
    if name == 'similar_restaurants':
        # ★ RAG 语义相似搜索
        try:
            if args.get('restaurant_id'):
                ref = next((x for x in restaurants if x['id'] == args['restaurant_id']), None)
                if not ref:
                    return {'error': '参照餐厅不存在', 'summary': '参照餐厅不存在'}
                qtext = rag.restaurant_text(ref)
                summary_head = f'与「{ref["name"]}」相似的餐厅'
            else:
                qtext = args.get('query') or ''
                if not qtext:
                    return {'error': '需要 restaurant_id 或 query', 'summary': '缺少参数'}
                summary_head = f'与「{qtext}」语义相似的餐厅'
            hits = rag.similar(qtext, top_n=10)
        except RuntimeError as e:
            return {'error': str(e), 'summary': 'RAG 未就绪（未配置 embedding Key 或未建索引）'}
        out = []
        for rid, score in hits:
            r = next((x for x in restaurants if x['id'] == rid), None)
            if r:
                out.append({**_compact_restaurant(r), 'similarity': round(score, 3)})
        return {'count': len(out), 'restaurants': out,
                'summary': f'{summary_head}，Top {len(out)}（按相似度降序）'}

    if name in WRITE_TOOLS:
        if not user_id:
            return {'error': '该操作需要先登录', 'summary': '操作需要登录'}
        rid = args.get('id')
        r = next((x for x in restaurants if x['id'] == rid), None)
        if not r:
            return {'error': '餐厅不存在', 'summary': '餐厅不存在'}
        return {'pending': True, 'name': name, 'args': args,
                'restaurant_name': r['name'],
                'summary': f'请求确认：{"收藏" if name == "add_favorite" else "打卡"}「{r["name"]}」'}

    if name == 'search_restaurants':
        pool = restaurants
        if args.get('major'):
            pool = [r for r in pool if args['major'] in r.get('tags', [])]
        if args.get('sub'):
            pool = [r for r in pool if (args['sub'] in r.get('subs', [])) or (r.get('sub') == args['sub'])]
        if args.get('max_price') is not None:
            pool = [r for r in pool if _parse_price(r.get('price', '')) <= args['max_price']]
        if args.get('min_price') is not None:
            pool = [r for r in pool if _parse_price(r.get('price', '')) >= args['min_price']]
        if args.get('max_dist') is not None:
            pool = [r for r in pool if _parse_dist(r.get('dist', '')) <= args['max_dist']]
        if args.get('no_booking'):
            pool = [r for r in pool if not r.get('book')]
        if args.get('michelin') is not None:
            pool = [r for r in pool if (r.get('michelin') or 0) >= args['michelin']]
        if args.get('green'):
            pool = [r for r in pool if r.get('green')]
        if args.get('feature'):
            pool = [r for r in pool if args['feature'] in r.get('features', [])]
        if args.get('has_promo'):
            pool = [r for r in pool if r.get('promo')]
        if args.get('keyword'):
            kw = args['keyword'].lower()
            pool = [r for r in pool if kw in (r['name'] + r['cuisine'] + r.get('desc', '')).lower()]
        result = {'count': len(pool), 'restaurants': [_compact_restaurant(r) for r in pool[:15]]}
        result['summary'] = f'筛选命中 {len(pool)} 家餐厅'
        if not pool:
            result['hint'] = '0 家命中。建议：去掉 keyword 改用 major/sub/max_price/feature 等结构化参数重试'
        return result

    if name == 'get_restaurant_detail':
        r = next((x for x in restaurants if x['id'] == args.get('id')), None)
        if not r:
            return {'error': '餐厅不存在', 'summary': '餐厅不存在'}
        reviews = db.filter_rows('reviews', lambda x: x['restaurantId'] == r['id'])
        reviews.sort(key=lambda x: x['createdAt'], reverse=True)
        avg = round(sum(x['rating'] for x in reviews) / len(reviews), 1) if reviews else None
        result = {
            'restaurant': _compact_restaurant(r),
            'full_desc': r.get('desc', ''), 'addr': r.get('addr', ''),
            'dishes': r.get('dishes', []),
            'avg_rating': avg, 'review_count': len(reviews),
            'reviews': [{'username': x['username'], 'rating': x['rating'], 'comment': x['comment'][:60]}
                        for x in reviews[:3]]
        }
        result['summary'] = f'已查询 {r["name"]} 详情（{len(reviews)} 条评价，均分 {avg}）'
        return result

    if name == 'get_leaderboard':
        rows = db.query('''
            SELECT restaurantId, ROUND(AVG(rating), 1) AS avg, COUNT(*) AS count
            FROM reviews GROUP BY restaurantId HAVING count >= 2
            ORDER BY avg DESC, count DESC LIMIT 10
        ''')
        board = []
        for row in rows:
            r = next((x for x in restaurants if x['id'] == row['restaurantId']), None)
            if r:
                board.append({'id': r['id'], 'name': r['name'], 'avg': row['avg'], 'count': row['count']})
        return {'leaderboard': board, 'summary': f'排行榜 {len(board)} 家'}

    if name == 'get_user_profile':
        if not user_id:
            return {'profile': '用户未登录，无画像数据', 'summary': '用户未登录'}
        user = db.find('users', lambda u: u['id'] == user_id)
        reviews = db.filter_rows('reviews', lambda x: x['userId'] == user_id)
        likes, dislikes = {}, {}
        for rv in reviews:
            r = next((x for x in restaurants if x['id'] == rv['restaurantId']), None)
            if not r:
                continue
            for t in r.get('tags', []):
                if rv['rating'] >= 4:
                    likes[t] = likes.get(t, 0) + 1
                elif rv['rating'] <= 2:
                    dislikes[t] = dislikes.get(t, 0) + 1
        favs = [next((x for x in restaurants if x['id'] == i), None) for i in (user or {}).get('favorites', [])]
        favs = [f for f in favs if f]
        fav_tags = {}
        for f in favs:
            for t in f.get('tags', []):
                fav_tags[t] = fav_tags.get(t, 0) + 1
        checkins = len(db.filter_rows('checkins', lambda c: c['userId'] == user_id))
        prefs = [p['content'] for p in load_user_preferences(user_id)[:10]]
        result = {
            'profile': {
                'liked_tags': sorted(likes.items(), key=lambda x: -x[1])[:5],
                'disliked_tags': sorted(dislikes.items(), key=lambda x: -x[1])[:3],
                'favorite_tags': sorted(fav_tags.items(), key=lambda x: -x[1])[:5],
                'review_count': len(reviews),
                'checkin_count': checkins,
                'long_term_preferences': prefs
            }
        }
        result['summary'] = f'已加载用户画像（{len(reviews)} 条评分 / {len(favs)} 家收藏 / {checkins} 次打卡）'
        return result

    return {'error': f'未知工具 {name}', 'summary': '未知工具'}


def _call_llm(messages, cfg, tools=None, temperature=0.7, model=None, retries=2):
    body = {'model': model or cfg.get('model', 'deepseek-chat'), 'messages': messages, 'temperature': temperature,
            'thinking': {'type': 'disabled'}}  # ★ 禁用思维链：默认模式先思考 6 秒才出正文
    if tools:
        body['tools'] = tools
    # 网络抖动重试（指数退避 1s/2s）
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            cfg.get('base_url', 'https://api.deepseek.com').rstrip('/') + '/v1/chat/completions',
            data=json.dumps(body).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', 'Bearer ' + cfg['api_key'])
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            last_err = e
            if attempt < retries:
                threading.Event().wait(attempt + 1)  # 退避 1s、2s
    raise last_err


SYSTEM_PROMPT = (
    '你是「随心吃吃」的餐厅推荐 Agent，服务香港大学周边的用户。\n'
    '你有 7 个工具：search_restaurants（筛选餐厅）、get_restaurant_detail（查详情与真实评价）、'
    'get_leaderboard（排行榜）、get_user_profile（用户口味画像）、'
    'similar_restaurants（语义相似搜索）、add_favorite 与 checkin_restaurant（执行类，需用户确认）。\n'
    '工作规则：\n'
    '1. 任何餐厅数据必须通过工具查询，绝不凭空编造；\n'
    '2. 复杂需求多步推理：先筛选 → 必要时调整一次筛选 → 给出结论；\n'
    '2.5 搜索结果已含简介摘要（desc 字段），直接基于摘要推荐即可；不要为每家公司逐个调用 get_restaurant_detail，'
    '除非用户明确询问某家详情；\n'
    '3. 每次调用工具前先想清楚参数是否可能命中；若一次查询命中 0 家，立即放宽条件'
    '（去掉关键词改用结构化参数，如 major/max_price），不要反复重试相同关键词；\n'
    '4. 只有当用户明确说「根据我的口味」之类的话时才调用 get_user_profile；\n'
    '5. no_booking 参数只在用户要求无需预约时使用；\n'
    '5.5 融合菜系要精确匹配：南美融合/日西融合/中西融合是不同的子类，'
    '用户点名某融合时用 sub 参数精确筛选（如 sub=南美融合），禁止用 keyword=\'融合\' 泛搜；\n'
    '5.6 用户问「像XX那样的店」「类似的店」「类似风格的」时用 similar_restaurants 工具做语义相似搜索；'
    '若该工具报「RAG 未就绪」，向用户说明需要配置 embedding 服务；\n'
    '6. 总计调用工具不超过 4 次就要开始写最终回答；\n'
    '7. add_favorite / checkin_restaurant 是执行类工具：只在用户明确说「收藏/记下」「打卡/我吃了」时才调用；'
    '调用后系统会请用户确认，确认后你应简短告知执行结果；\n'
    '8. 防幻觉铁律：只能推荐工具结果中出现过的餐厅；数据里没有的信息（营业时间/电话/订座方式/具体菜价）'
    '明确说「未提供」，绝不编造；不确定时如实说明；\n'
    '9. 最终回答用中文书写，不要提「工具」或「预算」等内部细节；'
    '回答末尾输出 JSON 数组 '
    '[{"id": 餐厅id, "reason": "20字内理由"}]，之后不要再输出任何内容。'
)


def review_answer(content, msgs, cfg):
    """评审 Agent（多 LLM 架构的 Reviewer 角色）：用便宜模型做事实核查
    输入：最终回答 + Agent 内部消息（含工具结果）
    输出：{passed, issues, fixed_answer}；失败返回 None（fail-open，不阻塞主流程）
    """
    try:
        # 取最近几条消息作为核查依据（工具结果可能很长，每段截断）
        evidence = []
        for m in msgs[-6:]:
            text = json.dumps(m.get('content') or m.get('tool_calls') or '', ensure_ascii=False)
            evidence.append({'role': m.get('role'), 'text': text[:900]})
        prompt = (
            '你是事实核查员。检查「待审核回答」中的每个事实是否与「依据数据」一致。\n'
            '检查要点：\n'
            '1. 提到的餐厅是否都出现在依据数据里；\n'
            '2. 价格/菜系/距离等数字是否与依据数据一致；\n'
            '3. 是否有依据数据里不存在的信息（营业时间/电话/订座方式等）被编造；\n'
            '4. JSON 数组里的 id 是否都在依据数据里出现过。\n'
            f'依据数据：{json.dumps(evidence, ensure_ascii=False)}\n'
            f'待审核回答：{content}\n'
            '输出 JSON：{"passed": true/false, "issues": ["问题描述"], '
            '"fixed_answer": "修正后的完整回答（删除编造内容，保留事实，保持 JSON 数组结尾）"}。'
            '只输出 JSON。'
        )
        resp = _call_llm([{'role': 'user', 'content': prompt}], cfg, temperature=0,
                         model=cfg.get('cheap_model', 'deepseek-flash'))
        text = resp['choices'][0]['message']['content']
        start, end = text.index('{'), text.rindex('}') + 1
        return json.loads(text[start:end])
    except Exception:
        return None


def parse_picks(content, restaurants):
    try:
        start, end = content.index('['), content.rindex(']') + 1
        picks = json.loads(content[start:end])
    except Exception:
        return []
    out = []
    for p in picks[:3]:
        r = next((x for x in restaurants if x['id'] == p.get('id')), None)
        if r:
            out.append({**r, 'reason': p.get('reason', '')})
    return out


def run_agent(history, user_id, cfg, temperature=0.7):
    """Agent 主循环（ReAct）：LLM ↔ 工具执行，最多 8 轮工具调用"""
    restaurants = load_restaurants()
    msgs = [{'role': 'system', 'content': SYSTEM_PROMPT}] + [
        {'role': m.get('role', 'user'), 'content': m.get('content', '')}
        for m in history[-10:]
    ]
    trace = []
    for _ in range(8):  # 防死循环（最多 8 轮工具调用）
        resp = _call_llm(msgs, cfg, tools=AGENT_TOOLS, temperature=temperature)
        msg = resp['choices'][0]['message']
        if msg.get('tool_calls'):
            msgs.append(msg)  # assistant 的 tool_calls 消息
            for tc in msg['tool_calls']:
                name = tc['function']['name']
                try:
                    args = json.loads(tc['function']['arguments'] or '{}')
                except Exception:
                    args = {}
                result = execute_tool(name, args, user_id, restaurants)
                trace.append({'tool': name, 'args': args, 'summary': result.get('summary', '')})
                msgs.append({'role': 'tool', 'tool_call_id': tc['id'],
                             'content': json.dumps(result, ensure_ascii=False)})
            continue
        content = msg.get('content') or ''
        # ★ 评审 Agent（与流式路径一致）
        review = review_answer(content, msgs, cfg)
        if review is not None:
            if review.get('passed'):
                trace.append({'tool': 'review', 'args': {}, 'summary': '✅ 事实核查通过'})
            else:
                content = review.get('fixed_answer') or content
                trace.append({'tool': 'review', 'args': {},
                              'summary': f'⚠️ 事实核查修正 {len(review.get("issues", []))} 处'})
        return {'reply': content, 'trace': trace, 'recommendations': parse_picks(content, restaurants)}
    return {'reply': '（Agent 思考步数超限，请换个说法试试）', 'trace': trace, 'recommendations': []}


def sse(obj):
    """SSE 帧：data: {json}\n\n"""
    return 'data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n'


# ==================== 多模态：图片识别（视觉模型） ====================
def identify_dish(image_base64, cfg):
    """用视觉模型识别食物图片 → 返回 (菜名, 描述)；失败返回 None"""
    emb = cfg.get('embedding', {})   # 复用 SiliconFlow 的 base_url/api_key
    vis = cfg.get('vision', {})
    if not emb.get('api_key'):
        raise RuntimeError('未配置 SiliconFlow API Key')
    body = json.dumps({
        'model': vis.get('model', 'Qwen/Qwen2.5-VL-7B-Instruct'),
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'text',
                 'text': '识别图片中的食物：输出 JSON {"dish": "菜名（如：避风塘香辣蟹）", "desc": "一句话描述（食材/口味/菜系，30字内）"}。只输出 JSON。'},
                {'type': 'image_url',
                 'image_url': {'url': 'data:image/jpeg;base64,' + image_base64}}
            ]
        }],
        'temperature': 0.2,
    }).encode('utf-8')
    req = urllib.request.Request(
        emb.get('base_url', 'https://api.siliconflow.cn').rstrip('/') + '/v1/chat/completions',
        data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer ' + emb['api_key'])
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode('utf-8'))
    content = result['choices'][0]['message']['content']
    try:
        start, end = content.index('{'), content.rindex('}') + 1
        return json.loads(content[start:end])
    except Exception:
        return None


# ==================== 偏好蒸馏（长期记忆） ====================
def load_user_preferences(user_id):
    rows = db.filter_rows('user_preferences', lambda p: p['userId'] == user_id)
    rows.sort(key=lambda p: p['createdAt'])
    return rows


def distill_preferences(history, user_id, cfg):
    """从对话抽取长期饮食偏好存入 SQLite（后台线程执行，失败不影响主流程）
    只收「长期有效」的偏好（忌口/预算习惯/菜系喜好），不收「今天想吃火锅」这类临时诉求
    """
    if not user_id or not history:
        return
    try:
        prompt = (
            '从下面的对话中抽取用户的饮食偏好，用于长期记忆。\n'
            '只抽取用户明确表达的、长期有效的偏好（口味禁忌/预算习惯/菜系喜好/忌口过敏/场景偏好），\n'
            '不要抽取临时性的（如「今天想吃火锅」「这顿随便吃点」）。\n'
            f'对话：{json.dumps(history[-6:], ensure_ascii=False)}\n'
            '输出 JSON：{"preferences": [{"content": "一句话偏好（以用户口吻，如：不吃辣/人均预算200以内/偏爱日料", '
            '"category": "口味/预算/菜系/场景"}]}。没有可抽取的就输出空数组。只输出 JSON。'
        )
        resp = _call_llm([{'role': 'user', 'content': prompt}], cfg, temperature=0.2)
        content = resp['choices'][0]['message']['content']  # 取回的是完整响应体，要解包
        start, end = content.index('{'), content.rindex('}') + 1
        items = json.loads(content[start:end]).get('preferences', [])
    except Exception:
        return
    existing = {p['content'] for p in load_user_preferences(user_id)}
    for it in items:
        c = (it.get('content') or '').strip()
        if not c or c in existing:
            continue
        db.insert('user_preferences', {'userId': user_id,
                                       'category': it.get('category', ''),
                                       'content': c})
        existing.add(c)
    # 上限 30 条，超出删最旧
    rows = load_user_preferences(user_id)
    for old in rows[:max(0, len(rows) - 30)]:
        db.delete('user_preferences', old['id'])


def distill_async(history, user_id, cfg):
    threading.Thread(target=distill_preferences, args=(history, user_id, cfg), daemon=True).start()


def _agent_loop_core(msgs, user_id, restaurants, cfg):
    """Agent 循环核心（生成器）：思考 → 工具 → 观察 → 再思考
    遇写操作工具时暂停（保存会话、发 pending_action 事件），由 /api/agent/confirm 恢复
    """
    for _ in range(8):  # 防死循环
        body = {
            'model': cfg.get('model', 'deepseek-chat'),
            'messages': msgs, 'temperature': 0.7,
            'tools': AGENT_TOOLS, 'stream': True,
            'thinking': {'type': 'disabled'}  # ★ 禁用思维链提速
        }
        req = urllib.request.Request(
            cfg.get('base_url', 'https://api.deepseek.com').rstrip('/') + '/v1/chat/completions',
            data=json.dumps(body).encode('utf-8'), method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', 'Bearer ' + cfg['api_key'])

        content_parts = []
        tool_calls = {}
        had_tool_calls = False

        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw_line in resp:
                line = raw_line.decode('utf-8').strip()
                if not line.startswith('data: '):
                    continue
                payload = line[6:]
                if payload == '[DONE]':
                    break
                chunk = json.loads(payload)
                delta = chunk['choices'][0].get('delta') or {}
                if delta.get('content'):
                    content_parts.append(delta['content'])
                    yield sse({'type': 'delta', 'text': delta['content']})
                for tc in delta.get('tool_calls') or []:
                    had_tool_calls = True
                    idx = tc.get('index', 0)
                    slot = tool_calls.setdefault(idx, {'id': '', 'name': '', 'args': []})
                    if tc.get('id'):
                        slot['id'] = tc['id']
                    if tc.get('function', {}).get('name'):
                        slot['name'] = tc['function']['name']
                    if tc.get('function', {}).get('arguments'):
                        slot['args'].append(tc['function']['arguments'])

        if had_tool_calls:
            tc_msgs = []
            for idx in sorted(tool_calls):
                t = tool_calls[idx]
                tc_msgs.append({'id': t['id'], 'type': 'function',
                                'function': {'name': t['name'], 'arguments': ''.join(t['args'])}})
            msgs.append({'role': 'assistant', 'content': None, 'tool_calls': tc_msgs})

            def parse_args(t):
                try:
                    return json.loads(''.join(t['args']) or '{}')
                except Exception:
                    return {}

            # ★ 并行执行：无写操作工具时多线程并发（写操作需确认，保持串行）
            has_write = any(t['name'] in WRITE_TOOLS for t in tool_calls.values())
            if has_write:
                ordered = [(idx, tool_calls[idx]) for idx in sorted(tool_calls)]
            else:
                with ThreadPoolExecutor(max_workers=4) as ex:
                    futures = {ex.submit(execute_tool, t['name'], parse_args(t), user_id, restaurants): (idx, t)
                               for idx, t in sorted(tool_calls.items())}
                    ordered = [(idx, t) for _, (idx, t) in
                               sorted((f.result() and None, (idx, t)) for f, (idx, t) in futures.items())]
                    results_by_idx = {idx: f.result() for f, (idx, t) in futures.items()}

            for idx, t in ordered:
                args = parse_args(t)
                result = results_by_idx[idx] if not has_write else execute_tool(t['name'], args, user_id, restaurants)
                if result.get('pending'):
                    # ★ 写操作：保存会话状态，暂停循环，等用户确认
                    sid = secrets.token_hex(16)
                    agent_sessions[sid] = {
                        'msgs': msgs, 'user_id': user_id, 'restaurants': restaurants,
                        'pending': {'tool_call_id': t['id'], 'name': t['name'], 'args': args},
                        'expires': datetime.now().timestamp() + 600
                    }
                    yield sse({'type': 'pending_action', 'session_id': sid,
                               'action': {'name': t['name'], 'args': args,
                                          'restaurant_name': result.get('restaurant_name', '')},
                               'summary': result.get('summary', '')})
                    return
                yield sse({'type': 'trace', 'tool': t['name'], 'summary': result.get('summary', '')})
                msgs.append({'role': 'tool', 'tool_call_id': t['id'],
                             'content': json.dumps(result, ensure_ascii=False)})
            continue

        content = ''.join(content_parts)
        # ★ 卡片立即出，评审放后台：先发 done，再做事实核查
        recs = parse_picks(content, restaurants)
        yield sse({'type': 'done', 'recommendations': recs})
        try:
            yield sse({'type': 'trace', 'tool': 'review', 'summary': '🔍 后台核查中...'})
            review = review_answer(content, msgs, cfg)
            if review is not None:
                if review.get('passed'):
                    yield sse({'type': 'trace', 'tool': 'review', 'summary': '✅ 事实核查通过'})
                else:
                    fixed = review.get('fixed_answer') or content
                    yield sse({'type': 'trace', 'tool': 'review',
                               'summary': f'⚠️ 事实核查修正 {len(review.get("issues", []))} 处'})
                    yield sse({'type': 'review_update', 'recommendations': parse_picks(fixed, restaurants)})
        except Exception:
            pass  # 核查失败不影响已输出的结果
        return

    yield sse({'type': 'error', 'text': '思考步数超限，请换个说法试试'})


def _execute_write(session, approved):
    """用户确认后真正执行写操作"""
    p = session['pending']
    user_id = session['user_id']
    if p['name'] == 'add_favorite':
        user = db.find('users', lambda u: u['id'] == user_id)
        favs = list(user['favorites'])
        if p['args']['id'] not in favs:
            favs.append(p['args']['id'])
        db.update('users', user_id, {'favorites': favs})
        return {'status': '已收藏', 'favorites': favs}
    if p['name'] == 'checkin_restaurant':
        today = datetime.now().strftime('%Y-%m-%d')
        dup = db.find('checkins', lambda c: c['userId'] == user_id
                       and c['restaurantId'] == p['args']['id']
                       and c['createdAt'].startswith(today))
        if dup:
            return {'status': '今天已经打过卡啦'}
        db.insert('checkins', {'userId': user_id,
                               'username': db.find('users', lambda u: u['id'] == user_id)['username'],
                               'restaurantId': p['args']['id']})
        return {'status': '打卡成功'}
    return {'status': '未知操作'}


def agent_loop(messages, user_id, cfg):
    """入口生成器：初始化上下文（注入长期偏好）后交给核心循环"""
    restaurants = load_restaurants()
    system_content = SYSTEM_PROMPT
    # ★ 长期记忆注入：蒸馏出来的偏好直接写进系统提示词，Agent 不用问就记得
    if user_id:
        prefs = load_user_preferences(user_id)
        if prefs:
            system_content += '\n用户长期偏好（已记住，推荐时主动遵守）：' + '；'.join(
                f'{p["category"]}：{p["content"]}' for p in prefs[:15])
    msgs = [{'role': 'system', 'content': system_content}] + [
        {'role': m.get('role', 'user'), 'content': m.get('content', '')}
        for m in messages[-10:]
    ]
    try:
        yield from _agent_loop_core(msgs, user_id, restaurants, cfg)
    except Exception as e:
        yield sse({'type': 'error', 'text': f'Agent 执行失败: {e}'})


@app.post('/api/agent/stream')
def agent_stream():
    """流式 Agent：思考过程（trace 事件）+ 最终答案逐字输出（delta 事件）+ 推荐（done 事件）"""
    cfg = load_config().get('llm', {})
    if not cfg.get('api_key'):
        return jsonify(error='未配置 LLM API Key（server/config.json）'), 503
    data = request.get_json(silent=True) or {}
    messages = data.get('messages')
    if not isinstance(messages, list) or not messages:
        return jsonify(error='缺少对话内容'), 400
    image_b64 = data.get('image')

    user_id = None
    header = request.headers.get('Authorization', '')
    token = header[7:] if header.startswith('Bearer ') else None
    if token:
        user = db.find('users', lambda u: u.get('token') == token)
        if user:
            user_id = user['id']

    def generate():
        msgs = messages  # ★ 不直接给外层变量赋值（否则闭包里 messages 变局部变量，纯文字时 UnboundLocalError）
        # ★ 查询缓存：单条消息的重复提问直接秒回（10 分钟 TTL）
        if len(msgs) == 1 and msgs[0].get('role') == 'user':
            key = msgs[0]['content']
            cached = agent_cache.get(key)
            if cached and time.time() - cached['t'] < 600:
                yield sse({'type': 'trace', 'tool': 'cache', 'summary': '⚡ 命中缓存'})
                reply = cached['reply']
                for i in range(0, len(reply), 40):  # 快速重放（不打字机动画）
                    yield sse({'type': 'delta', 'text': reply[i:i + 40]})
                yield sse({'type': 'done', 'recommendations': cached['recommendations']})
                return
        # ★ 进度事件：流一建立就发，UI 不会"卡住"无反馈
        yield sse({'type': 'trace', 'tool': 'start', 'summary': '🤖 开始分析需求...'})
        # ★ 多模态：图片识别在流内执行，边识别边报进度
        if image_b64:
            yield sse({'type': 'trace', 'tool': 'vision', 'summary': '📷 识别图片中...'})
            try:
                dish = identify_dish(image_b64, load_config())
                if dish:
                    yield sse({'type': 'trace', 'tool': 'vision',
                               'summary': f'📷 识别完成：{dish["dish"]}'})
                    msgs = [{'role': 'user',
                             'content': f'[图片识别：{dish["dish"]}。{dish["desc"]}] 请根据这道菜推荐做得好的餐厅。'}
                            ] + msgs
                else:
                    yield sse({'type': 'trace', 'tool': 'vision', 'summary': '📷 图片未能识别，按文字继续'})
            except Exception:
                yield sse({'type': 'trace', 'tool': 'vision', 'summary': '📷 识别失败，按文字继续'})
        # ★ 收集本轮事件，完成后写入查询缓存（仅单条消息的首次提问）
        collectable = len(msgs) == 1 and msgs[0].get('role') == 'user' and not image_b64
        buf = []
        for ev_raw in agent_loop(msgs, user_id, cfg):
            buf.append(ev_raw)
            yield ev_raw
        if collectable:
            try:
                evs = [json.loads(e[6:]) for e in buf if e.startswith('data: ')]
                done_ev = next((e for e in evs if e.get('type') == 'done'), None)
                if done_ev:
                    reply = ''.join(e.get('text', '') for e in evs if e.get('type') == 'delta')
                    agent_cache[msgs[0]['content']] = {
                        't': time.time(), 'reply': reply,
                        'recommendations': done_ev.get('recommendations', [])}
            except Exception:
                pass
        distill_async(msgs, user_id, cfg)  # ★ 对话结束后后台抽取长期偏好

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/agent/confirm')
def agent_confirm():
    """写操作确认：{session_id, approved} → 执行（或拒绝）→ 恢复 Agent 循环（继续流式）"""
    data = request.get_json(silent=True) or {}
    sid = data.get('session_id')
    sess = agent_sessions.pop(sid, None) if sid else None
    if not sess or sess['expires'] < datetime.now().timestamp():
        return jsonify(error='会话不存在或已过期'), 410

    approved = bool(data.get('approved'))
    p = sess['pending']
    result = _execute_write(sess, approved) if approved else {'status': '用户取消了操作'}
    # 把确认结果作为 tool 消息回填，恢复循环
    sess['msgs'].append({'role': 'tool', 'tool_call_id': p['tool_call_id'],
                         'content': json.dumps(result, ensure_ascii=False)})

    def generate():
        try:
            yield from _agent_loop_core(sess['msgs'], sess['user_id'], sess['restaurants'],
                                       load_config().get('llm', {}))
            distill_async([], sess['user_id'], load_config().get('llm', {}))
        except Exception as e:
            yield sse({'type': 'error', 'text': f'Agent 执行失败: {e}'})

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/agent/chat')
def agent_chat():
    """Agent 对话入口：前端带历史，服务端跑 Agent 循环并返回思考过程"""
    cfg = load_config().get('llm', {})
    if not cfg.get('api_key'):
        return jsonify(error='未配置 LLM API Key（server/config.json）'), 503
    data = request.get_json(silent=True) or {}
    messages = data.get('messages')
    if not isinstance(messages, list) or not messages:
        return jsonify(error='缺少对话内容'), 400
    # ★ 多模态：附带图片时先用视觉模型识别，注入对话上下文
    image_b64 = data.get('image')
    if image_b64:
        try:
            dish = identify_dish(image_b64, load_config())
            if dish:
                messages = [{'role': 'user',
                             'content': f'[图片识别：{dish["dish"]}。{dish["desc"]}] 请根据这道菜推荐做得好的餐厅。'}
                            ] + messages
        except Exception:
            pass  # 识别失败则纯文本继续

    # 从 Authorization 提取用户（可选登录，用于画像工具）
    user_id = None
    header = request.headers.get('Authorization', '')
    token = header[7:] if header.startswith('Bearer ') else None
    if token:
        user = db.find('users', lambda u: u.get('token') == token)
        if user:
            user_id = user['id']

    try:
        result = run_agent(messages, user_id, cfg)
    except Exception as e:
        return jsonify(error=f'Agent 执行失败: {e}'), 502
    return jsonify(reply=result['reply'], trace=result['trace'],
                   recommendations=result['recommendations'])


# ==================== 排行榜 ====================
# 旧版：Python 读全表分组统计（约 12 行）
# 新版：一条 SQL 搞定 —— GROUP BY 分组 + AVG/COUNT 聚合 + HAVING 过滤 + ORDER BY/LIMIT 排序取前10
@app.get('/api/leaderboard')
def leaderboard():
    rows = db.query('''
        SELECT restaurantId,
               ROUND(AVG(rating), 1) AS avg,
               COUNT(*) AS count
        FROM reviews
        GROUP BY restaurantId
        HAVING count >= 2      -- 少于 2 条不参与（防刷分）
        ORDER BY avg DESC, count DESC
        LIMIT 10
    ''')
    return jsonify(leaderboard=rows)


# ==================== 健康检查 ====================
@app.get('/api/health')
def health():
    return jsonify(ok=True, time=datetime.now().isoformat())


if __name__ == '__main__':
    db.init()  # 建表（幂等）
    rag.init_table()  # RAG 向量表
    print(f'🍜 随心吃吃后端已启动: http://localhost:{PORT}')
    print('   接口: /api/register /api/login /api/me /api/favorites /api/reviews /api/leaderboard')
    app.run(host='127.0.0.1', port=PORT, debug=False)
