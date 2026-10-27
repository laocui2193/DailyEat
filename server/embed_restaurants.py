# -*- coding: utf-8 -*-
"""把全部餐厅向量化并存入 SQLite（RAG 离线索引构建）
用法：python embed_restaurants.py（先配好 server/config.json 的 embedding 段）
幂等：重复运行会全量重建向量索引（餐厅数据变了就重跑）
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).parent.parent
import rag

BATCH = 32  # 每批 32 家，一次 embedding 调用


def main():
    cfg = rag.load_embedding_cfg()
    if not cfg.get('api_key'):
        print('❌ 未配置 embedding API Key')
        print('   去 https://siliconflow.cn 注册（免费）→ 生成 API Key →')
        print('   填入 server/config.json 的 embedding.api_key，再跑本脚本')
        return 1

    rag.init_table()
    data = json.loads((ROOT / 'restaurants.json').read_text(encoding='utf-8'))
    restaurants = [r for r in data['restaurants'] if not r.get('closed')]

    all_pairs = []
    for i in range(0, len(restaurants), BATCH):
        chunk = restaurants[i:i + BATCH]
        texts = [rag.restaurant_text(r) for r in chunk]
        try:
            vectors = rag.embed_texts(texts, cfg)
        except Exception as e:
            print(f'  批次 {i // BATCH + 1} 失败: {e}')
            return 1
        for r, v in zip(chunk, vectors):
            all_pairs.append((r['id'], v))
        print(f'  批次 {i // BATCH + 1} 完成（{len(chunk)} 家）')

    rag.store_embeddings(all_pairs)
    print(f'\n✅ 已向量化 {len(all_pairs)} 家餐厅（含停业的除外），索引在 SQLite embeddings 表')
    return 0


if __name__ == '__main__':
    sys.exit(main())
