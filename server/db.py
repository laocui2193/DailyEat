# -*- coding: utf-8 -*-
"""数据层：SQLite 数据库（Python 内置 sqlite3，零额外依赖）

接口与旧 JSON 版完全一致，上层 app.py 无感知：
    insert(name, row) / find(name, pred) / filter_rows(name, pred)
    update(name, id, patch) / read_table(name) / query(sql)

建表语句（SQL）：
    users   用户表：id, username(唯一), salt, hash, token, favorites(JSON文本), createdAt
    reviews 评价表：id, userId, username, restaurantId, rating, comment, createdAt
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / 'data' / 'nearbyeats.db'

# 需要 JSON 序列化的字段（SQLite 没有数组类型，存 JSON 文本）
_JSON_FIELDS = {'users': ['favorites'], 'submissions': ['dishes']}


def _conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 查询结果支持 r['列名'] 访问
    return conn


def init():
    """建表（IF NOT EXISTS = 幂等，重复调用安全）"""
    with _conn() as c:
        c.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                salt TEXT NOT NULL,
                hash TEXT NOT NULL,
                token TEXT,
                favorites TEXT NOT NULL DEFAULT '[]',
                createdAt TEXT
            );
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId INTEGER NOT NULL,
                username TEXT NOT NULL,
                restaurantId INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT NOT NULL DEFAULT '',
                createdAt TEXT
            );
            CREATE TABLE IF NOT EXISTS checkins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId INTEGER NOT NULL,
                username TEXT NOT NULL,
                restaurantId INTEGER NOT NULL,
                createdAt TEXT
            );
            CREATE TABLE IF NOT EXISTS submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId INTEGER NOT NULL,
                username TEXT NOT NULL,
                name TEXT NOT NULL,
                cuisine TEXT NOT NULL,
                tag TEXT NOT NULL,
                price TEXT NOT NULL,
                dist TEXT NOT NULL,
                addr TEXT NOT NULL,
                desc TEXT NOT NULL DEFAULT '',
                dishes TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                createdAt TEXT
            );
            CREATE TABLE IF NOT EXISTS user_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                userId INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                createdAt TEXT
            );
        ''')


def _to_python(name, row):
    """SQLite 行 → dict，并把 JSON 文本字段还原成 Python 对象"""
    d = dict(row)
    for field in _JSON_FIELDS.get(name, []):
        try:
            d[field] = json.loads(d[field] or '[]')
        except (ValueError, TypeError):
            d[field] = []
    return d


def _to_db(name, row):
    """Python dict → SQLite 行，把 list 字段序列化成 JSON 文本"""
    d = dict(row)
    for field in _JSON_FIELDS.get(name, []):
        if field in d and isinstance(d[field], list):
            d[field] = json.dumps(d[field], ensure_ascii=False)
    return d


def read_table(name):
    # 表名来自我们自己的代码（白名单），不是用户输入，无注入风险
    with _conn() as c:
        rows = c.execute(f'SELECT * FROM {name}').fetchall()
    return [_to_python(name, r) for r in rows]


def insert(name, row):
    row = _to_db(name, row)
    row.setdefault('createdAt', datetime.now(timezone.utc).isoformat())
    cols = ', '.join(row.keys())
    marks = ', '.join('?' * len(row))  # ? 占位符 = 参数化查询，防注入
    with _conn() as c:
        cur = c.execute(f'INSERT INTO {name} ({cols}) VALUES ({marks})', list(row.values()))
        row['id'] = cur.lastrowid  # AUTOINCREMENT 生成的 id
    # 返回前把 JSON 文本字段还原成 Python 对象（与 read_table 一致）
    return _to_python(name, row)


def find(name, pred):
    for r in read_table(name):
        if pred(r):
            return r
    return None


def filter_rows(name, pred):
    return [r for r in read_table(name) if pred(r)]


def update(name, row_id, patch):
    patch = _to_db(name, patch)
    sets = ', '.join(f'{k} = ?' for k in patch)
    with _conn() as c:
        c.execute(f'UPDATE {name} SET {sets} WHERE id = ?', [*patch.values(), row_id])
    return find(name, lambda r: r['id'] == row_id)


def query(sql, params=()):
    """直接执行 SQL，给排行榜这类聚合查询用"""
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def delete(name, row_id):
    with _conn() as c:
        c.execute(f'DELETE FROM {name} WHERE id = ?', [row_id])
