# -*- coding: utf-8 -*-
"""RAG 模块：Embedding 向量化 + 余弦相似度检索
流程：餐厅文本 → embedding API → 向量存 SQLite → 查询时算余弦相似度
向量模型：OpenAI 兼容接口（默认硅基流动 BAAI/bge-m3）
"""
import json
import math
import sqlite3
import urllib.request
from pathlib import Path

DB_PATH = Path(__file__).parent / 'data' / 'nearbyeats.db'
CONFIG_PATH = Path(__file__).parent / 'config.json'


def load_embedding_cfg():
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return cfg.get('embedding', {})


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_table():
    with _conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS embeddings (
            restaurant_id INTEGER PRIMARY KEY,
            vector TEXT NOT NULL
        )''')


def embed_texts(texts, cfg=None):
    """批量向量化（bge-m3 支持数组输入，一次调用一批）"""
    cfg = cfg or load_embedding_cfg()
    if not cfg.get('api_key'):
        raise RuntimeError('未配置 embedding API Key（server/config.json 的 embedding 段）')
    body = json.dumps({'model': cfg.get('model', 'BAAI/bge-m3'),
                       'input': texts}).encode('utf-8')
    req = urllib.request.Request(
        cfg.get('base_url', 'https://api.siliconflow.cn').rstrip('/') + '/v1/embeddings',
        data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer ' + cfg['api_key'])
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode('utf-8'))
    # OpenAI 兼容格式：data 按 input 顺序返回
    return [d['embedding'] for d in result['data']]


def store_embeddings(pairs):
    """pairs: [(restaurant_id, vector)] 全量覆盖写入"""
    with _conn() as c:
        c.executemany('INSERT OR REPLACE INTO embeddings (restaurant_id, vector) VALUES (?, ?)',
                      [(rid, json.dumps(v)) for rid, v in pairs])


def load_all_vectors():
    with _conn() as c:
        rows = c.execute('SELECT restaurant_id, vector FROM embeddings').fetchall()
    return [(r['restaurant_id'], json.loads(r['vector'])) for r in rows]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def similar(query_text, top_n=10, cfg=None):
    """语义相似检索：query_text → 向量 → 与全库余弦相似度 → Top N [(restaurant_id, score)]"""
    cfg = cfg or load_embedding_cfg()
    qv = embed_texts([query_text], cfg)[0]
    scored = [(rid, cosine(qv, v)) for rid, v in load_all_vectors()]
    scored.sort(key=lambda x: -x[1])
    return scored[:top_n]


def restaurant_text(r):
    """把餐厅记录拼成一段用于向量化的文本"""
    parts = [r.get('name', ''), r.get('cuisine', ''), r.get('sub', ''),
             r.get('desc', '')] + (r.get('features') or []) + (r.get('dishes') or [])
    return ' '.join(str(p) for p in parts if p)


def count_stored():
    with _conn() as c:
        return c.execute('SELECT COUNT(*) FROM embeddings').fetchone()[0]
