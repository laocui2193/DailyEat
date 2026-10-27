# -*- coding: utf-8 -*-
"""一次性脚本：用 LLM 给全部餐厅批量打场景特征标签
解决「关键词搜不到语义」问题：如简介写"维港景色"的餐厅，LLM 会打上"海景"标签。
用法：python enrich_features.py（读取 server/config.json 的 LLM 配置）
产物：restaurants.json 每家的 "features": ["海景", ...] 字段（可重复运行，增量更新）
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).parent.parent
RESTAURANTS_PATH = ROOT / 'restaurants.json'
CONFIG_PATH = Path(__file__).parent / 'config.json'

FEATURE_POOL = ['海景', '夜景', '户外露台', '约会氛围', '安静', '亲子友好',
                '排队热门', '快食', '深夜营业', '商务宴请', '名人打卡',
                '高层视野', '河景', '浪漫', '性价比高', '老字号']


def call_llm(prompt, cfg):
    body = json.dumps({
        'model': cfg.get('model', 'deepseek-chat'),
        'messages': [{'role': 'user', 'content': prompt}],
        'temperature': 0.1,  # 打标任务要稳定
    }).encode('utf-8')
    req = urllib.request.Request(
        cfg.get('base_url', 'https://api.deepseek.com').rstrip('/') + '/v1/chat/completions',
        data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer ' + cfg['api_key'])
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode('utf-8'))
    return result['choices'][0]['message']['content']


def main():
    cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))['llm']
    if not cfg.get('api_key'):
        print('未配置 LLM API Key（server/config.json）')
        return

    data = json.loads(RESTAURANTS_PATH.read_text(encoding='utf-8'))
    restaurants = data['restaurants']

    # 按 40 家一批，避免单次输出过长
    features_map = {}
    for i in range(0, len(restaurants), 40):
        batch = restaurants[i:i + 40]
        menu = [{'id': r['id'], 'name': r['name'], 'cuisine': r['cuisine'],
                 'addr': r.get('addr', ''), 'desc': r.get('desc', '')} for r in batch]
        prompt = (
            f'候选特征标签：{"、".join(FEATURE_POOL)}\n'
            '给以下每家餐厅打 0-4 个最贴切的特征标签（从候选里选，不要自创新标签）。\n'
            f'餐厅列表：{json.dumps(menu, ensure_ascii=False)}\n'
            '只输出 JSON：{"features": {"餐厅id": ["标签1","标签2"], ...}}，不要其他内容。'
        )
        content = call_llm(prompt, cfg)
        try:
            start, end = content.index('{'), content.rindex('}') + 1
            parsed = json.loads(content[start:end])['features']
            for rid, feats in parsed.items():
                features_map[int(rid)] = [f for f in feats if f in FEATURE_POOL]
        except Exception as e:
            print(f'第 {i}-{i+40} 批解析失败: {e}，跳过')
        print(f'批次 {i // 40 + 1} 完成（{len(batch)} 家）')

    print(f'\n共获得 {len(features_map)} 家特征标签')

    # 行级插入，保持文件原有格式（每家把 features 插到 "closed":false 前）
    lines = RESTAURANTS_PATH.read_text(encoding='utf-8').split('\n')
    inserted = 0
    for i, line in enumerate(lines):
        for rid, feats in features_map.items():
            if line.strip().startswith('{ "id":%d,' % rid):
                target = lines[i + 1]
                if '"closed":false' in target and '"features"' not in target:
                    lines[i + 1] = target.replace(
                        '"closed":false',
                        '"features": %s, "closed":false' % json.dumps(feats, ensure_ascii=False))
                    inserted += 1
    RESTAURANTS_PATH.write_text('\n'.join(lines), encoding='utf-8')
    print(f'已写入 {inserted} 家 features 字段')


if __name__ == '__main__':
    main()
