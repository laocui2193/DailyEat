# -*- coding: utf-8 -*-
"""推荐覆盖验证：622 家餐厅是否都能被推荐出来
检查项：
  A. 停业标记（closed）—— 应被排除（预期内）
  B. 价格在滑块范围外（<HK$30 或 >HK$4000）—— 默认参数下永远刷不出（BUG！）
  C. 缺少关键字段（tags/name/dishes）
  D. 默认筛选下 2000 次模拟推荐，统计从未出现的餐厅
  E. 各用餐时段的覆盖情况（烧烤/火锅不进午餐是设计如此，确认数量）
"""
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).parent.parent
data = json.loads((ROOT / 'restaurants.json').read_text(encoding='utf-8'))
ALL = data['restaurants']
N = len(ALL)

print(f'总餐厅数: {N}')

# ---- A. 停业 ----
closed = [r for r in ALL if r.get('closed')]
print(f'\n[A] 停业标记: {len(closed)} 家（预期被排除）')
for r in closed[:8]:
    print('   ', r['id'], r['name'])

# ---- B. 价格范围 ----
def price_of(r):
    s = ''.join(ch for ch in str(r.get('price', '')) if ch.isdigit())
    return int(s or 0)

MIN_BUDGET, MAX_BUDGET = 30, 4000
out_of_range = [r for r in ALL if not r.get('closed') and not (MIN_BUDGET <= price_of(r) <= MAX_BUDGET)]
print(f'\n[B] 价格超出滑块范围（默认 HK$30-4000）: {len(out_of_range)} 家')
for r in out_of_range:
    print(f'    ❌ id={r["id"]} {r["name"]} price={r.get("price")}')

# ---- C. 缺关键字段 ----
bad_fields = [r for r in ALL if not r.get('name') or not isinstance(r.get('tags'), list)
              or not isinstance(r.get('dishes'), list)]
print(f'\n[C] 缺关键字段: {len(bad_fields)} 家')
for r in bad_fields:
    print('    ❌', r.get('id'), r.get('name'))

# ---- D. 模拟推荐（默认无筛选，排除近3天）----
# 与前端 pickThree 无偏好分支一致
def parse_int(s):
    return int(''.join(ch for ch in str(s) if ch.isdigit()) or 0)

def pick_three(recent_ids, current_ids):
    exclude = set(recent_ids) | set(current_ids)
    pool = [r for r in ALL if not r.get('closed') and r['id'] not in exclude]
    pool = [r for r in pool if MIN_BUDGET <= parse_int(r.get('price', '')) <= MAX_BUDGET]
    pool = [r for r in pool if r.get('tags')]  # 空 tags 只能在无偏好时出现，这里保留？不，前端无此过滤
    pool = [r for r in pool]  # (去掉上行的空 tags 过滤，保持与前端一致)
    # 菜系去重 + 随机
    random.shuffle(pool)
    picked, cuisines = [], set()
    for r in pool:
        if len(picked) >= 3:
            break
        if r['cuisine'] not in cuisines:
            picked.append(r)
            cuisines.add(r['cuisine'])
    for r in pool:
        if len(picked) >= 3:
            break
        if r not in picked:
            picked.append(r)
    return [r['id'] for r in picked[:3]]

random.seed(42)
from collections import deque
recent_ids = deque(maxlen=9)  # 模拟前端：近 3 天 + 今天，共 9 个 id 滚动
seen = Counter()
for day in range(1000):
    picks = pick_three(set(recent_ids), [])
    for pid in picks:
        seen[pid] += 1
        recent_ids.append(pid)

never_seen = [r for r in ALL if not r.get('closed') and MIN_BUDGET <= price_of(r) <= MAX_BUDGET
              and r['id'] not in seen]
print(f'\n[D] 1000 天模拟推荐（每天3家=3000次）')
print(f'    出现过: {len(seen)} 家 | 从未出现: {len(never_seen)} 家')
for r in never_seen:
    print(f'    ❌ id={r["id"]} {r["name"]}')

# ---- E. 用餐时段覆盖 ----
MEAL_TAGS = {
    '早餐(6-9)': ['早茶点心', '简餐茶餐'],
    '午餐(10-13)': ['中式正餐', '简餐茶餐', '日本料理', '韩国料理', '东南亚菜',
                    '西餐欧陆', '拉美风味', '印度料理', '海鲜水产', '创意融合'],
    '晚餐(14-20)': None,
    '宵夜(21-5)': ['火锅煲类', '烧烤烤肉', '简餐茶餐'],
}
print('\n[E] 各时段可出现的餐厅数（时段过滤是设计行为）')
for meal, tags in MEAL_TAGS.items():
    if tags is None:
        print(f'    {meal}: 全部 {N - len(closed)} 家（晚餐不过滤）')
    else:
        ok = [r for r in ALL if not r.get('closed') and any(t in r.get('tags', []) for t in tags)]
        print(f'    {meal}: {len(ok)} 家可用')
        excluded = [r for r in ALL if not r.get('closed')
                    and not any(t in r.get('tags', []) for t in tags)]
        kinds = Counter(tuple(r.get('tags', [])) for r in excluded)
        print(f'        被排除 {len(excluded)} 家，主要标签: {kinds.most_common(4)}')
