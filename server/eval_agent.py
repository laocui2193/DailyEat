# -*- coding: utf-8 -*-
"""Agent 评测集：改提示词/工具后跑一遍，防回归
用法：python eval_agent.py
断言策略：temperature=0.2 低温求稳定；断言用「期望集合命中 ≥ N」而非精确匹配
"""
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

from app import run_agent, load_config, load_restaurants

RESTAURANTS = load_restaurants()

# id → 数据（断言用）
def by_ids(*ids):
    return {i for i in ids}


# 每道题：query + 断言
TESTS = [
    # ---- 筛选能力 ----
    {'name': '拉面预算过滤', 'query': '推荐人均200以内的拉面',
     'expect': by_ids(37, 130, 131, 132, 133, 134), 'min_hits': 1,
     'forbid': by_ids(126)},  # 不推高人均店
    {'name': '火锅预算过滤', 'query': '想吃人均300以内的火锅',
     'expect': by_ids(39, 112, 113, 114, 115, 116), 'min_hits': 1},
    {'name': '米其林三星', 'query': '推荐一家米其林三星餐厅',
     'expect': by_ids(47, 82, 89, 91, 109, 110, 123), 'min_hits': 1,
     'forbid': by_ids(15, 22)},  # 不推无星店
    {'name': '海景语义匹配', 'query': '想找有海景的餐厅吃晚饭',
     'expect': by_ids(49, 82, 92, 121, 124, 136, 144, 163, 164, 165), 'min_hits': 1},
    {'name': '烧鹅名店', 'query': '想吃烧鹅',
     'expect': by_ids(16, 31, 17), 'min_hits': 1},
    {'name': '避风塘炒蟹', 'query': '想食避风塘炒蟹',
     'expect': by_ids(33, 178), 'min_hits': 1},
    {'name': '糖水甜品', 'query': '饭后想吃糖水',
     'expect': by_ids(6, 9, 21, 27, 36, 43), 'min_hits': 1},
    {'name': '牛腩面', 'query': '想吃牛腩面',
     'expect': by_ids(15), 'min_hits': 1},
    {'name': '海景粤菜多步', 'query': '吃有海景的粤菜',
     'expect': by_ids(92, 163, 164), 'min_hits': 1},
    {'name': '平价海鲜', 'query': '想吃平价海鲜',
     'expect': by_ids(180, 179), 'min_hits': 1},
    # ---- 约束能力 ----
    {'name': '距离约束', 'query': '离港大1.5公里以内的餐厅推荐',
     'assert': 'dist_le_1500', 'expect': None},
    {'name': '无需预约', 'query': '不用订位、随到随吃的餐厅',
     'assert': 'no_book', 'expect': None},
    # ---- 场景能力 ----
    {'name': '约会场景', 'query': '适合约会的西餐厅',
     'assert': 'feature_in', 'feature': ['约会氛围', '浪漫'], 'expect': None},
    {'name': '深夜宵夜', 'query': '深夜凌晨想吃宵夜',
     'expect': by_ids(10, 11, 42, 33, 160), 'min_hits': 1},
    {'name': '学生平价', 'query': '学生党平价的午晚饭',
     'assert': 'price_le_100', 'expect': None},
    # ---- 幻觉专项用例 ----
    {'name': '不存在餐厅查询', 'query': '帮我查查「飞天面馆」怎么样',
     'assert': 'reply_contains', 'texts': ['找不到', '没有找到', '不存在', '没有这家', '没有名为'],
     'expect': None},
    {'name': '未提供数据不编造', 'query': 'Caprice 的电话号码是多少',
     'assert': 'reply_contains', 'texts': ['未提供', '没有', '暂无'], 'expect': None},
    # ---- 622 家数据库新增用例 ----
    {'name': '南美融合精确', 'query': '推荐南美融合菜',
     'expect': by_ids(84), 'min_hits': 1,
     'forbid': by_ids(81, 86, 87, 93)},  # 不许推日西/中西融合（用户实测踩坑案例）
    {'name': '中法融合精确', 'query': '推荐中法融合的餐厅',
     'expect': by_ids(81, 87), 'min_hits': 1,
     'forbid': by_ids(86, 93)},  # 日西融合（Ando/Arbor）是不同概念
    {'name': '餐厅周优惠', 'query': '餐厅周有什么优惠餐厅推荐',
     'assert': 'has_promo', 'expect': None},
    # ---- 多轮能力 ----
    {'name': '多轮换一批', 'query': None, 'multi': True},
]


def check(test, result, prev_ids=None):
    ids = [r['id'] for r in result['recommendations']]
    if not ids:
        return False, '没有输出推荐'
    if test.get('assert') == 'dist_le_1500':
        ok = all(int(''.join(ch for ch in str(r['dist']) if ch.isdigit())) <= 1500
                 for r in result['recommendations'])
        return ok, f'距离: {[r["dist"] for r in result["recommendations"]]}'
    if test.get('assert') == 'no_book':
        ok = all(not r.get('book') for r in result['recommendations'])
        return ok, f'预约字段: {[bool(r.get("book")) for r in result["recommendations"]]}'
    if test.get('assert') == 'price_le_100':
        ok = all(int(''.join(ch for ch in str(r['price']) if ch.isdigit())) <= 100
                 for r in result['recommendations'])
        return ok, f'价格: {[r["price"] for r in result["recommendations"]]}'
    if test.get('multi'):
        overlap = set(ids) & set(prev_ids or [])
        return not overlap, f'与上一轮重叠: {overlap or "无"}'
    if test.get('assert') == 'feature_in':
        # 断言推荐结果都带有指定场景特征（测能力而非测特定店）
        feats = test.get('feature') or []
        bad = [r['name'] for r in result['recommendations']
               if not any(f in (r.get('features') or []) for f in feats)]
        return not bad, f'缺特征: {bad or "无"}'
    if test.get('assert') == 'has_promo':
        # 断言所有推荐都带优惠活动字段
        bad = [r['name'] for r in result['recommendations'] if not r.get('promo')]
        return not bad, f'无优惠: {bad or "无"}'
    if test.get('assert') == 'reply_contains':
        # 断言回复文本包含指定关键词（防幻觉：诚实回答）
        reply = result.get('reply', '')
        hit = next((t for t in test.get('texts', []) if t in reply), None)
        return hit is not None, f'回复含「{hit}」' if hit else f'回复不含任一关键词: {reply[:60]}'
    hits = [i for i in ids if i in (test.get('expect') or set())]
    forbid_hits = [i for i in ids if i in (test.get('forbid') or set())]
    ok = len(hits) >= test.get('min_hits', 1) and not forbid_hits
    names = {r['id']: r['name'] for r in RESTAURANTS}
    detail = f'命中 {[names.get(i, i) for i in hits]}'
    if forbid_hits:
        detail += f' 违规 {[names.get(i, i) for i in forbid_hits]}'
    return ok, detail


def main():
    cfg = load_config()['llm']
    if not cfg.get('api_key'):
        print('未配置 LLM API Key')
        return 1

    passed = 0
    prev_ids = []
    for i, t in enumerate(TESTS, 1):
        if t.get('multi'):
            t['query'] = '换3家跟刚才不一样的'
        history = [{'role': 'user', 'content': t['query']}]
        if t.get('multi'):
            history = [{'role': 'user', 'content': '推荐人均200以内的拉面'},
                       {'role': 'assistant', 'content': '（上一轮已推荐 3 家）'},
                       {'role': 'user', 'content': t['query']}]
        result = run_agent(history, None, cfg, temperature=0.2)
        ids = [r['id'] for r in result['recommendations']]
        ok, detail = check(t, result, prev_ids)
        prev_ids = ids
        status = '✅' if ok else '❌'
        print(f'{status} [{i:>2}/{len(TESTS)}] {t["name"]}: {detail}')
        passed += ok

    print(f'\n通过 {passed}/{len(TESTS)}')
    return 0 if passed == len(TESTS) else 1


if __name__ == '__main__':
    sys.exit(main())
