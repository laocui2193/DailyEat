# -*- coding: utf-8 -*-
"""香港餐厅周优惠抓取流水线（网页 → 结构化 API 数据）
三步：
  ① fetch     —— urllib 抓网页 HTML
  ② extract   —— LLM 从 HTML 文本抽取结构化 JSON（餐厅名/套餐价/时段）
  ③ match+write —— LLM 模糊匹配到本地数据库 → 写 restaurants.json 的 promo 字段
用法：python fetch_restaurant_week.py（可重复运行，promo 字段覆盖更新）
"""
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).parent.parent
RESTAURANTS_PATH = ROOT / 'restaurants.json'
CONFIG_PATH = Path(__file__).parent / 'config.json'

# 候选来源（按顺序尝试，抓到有内容的就用）
SOURCE_URLS = [
    'https://restaurantweek.diningcity.hk/lang/zh-HK/cities/hongkong',
    'https://www.diningcity.hk/zh-HK/pages/rwhk_aboutus_2603?embed_mode=true',
]

# ★ 官方 API（从 DevTools Network 挖出来的裸接口）
API_RESTAURANTS = ('https://api.diningcity.asia/public/restaurants'
                   '?project=rwhk_summer_2026&lang=zh-HK&region_id=28&platform=web&per_page=100&page={page}')
API_PROJECT_CONFIG = ('https://api.diningcity.asia/public/project_configs/'
                      'rwhk_summer_2026?lang=zh-HK&region_id=28&platform=web')


def load_cfg():
    return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))['llm']


def call_llm(prompt, cfg, temperature=0.1):
    body = json.dumps({'model': cfg.get('model', 'deepseek-chat'),
                       'messages': [{'role': 'user', 'content': prompt}],
                       'temperature': temperature}).encode('utf-8')
    req = urllib.request.Request(
        cfg.get('base_url', 'https://api.deepseek.com').rstrip('/') + '/v1/chat/completions',
        data=body, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Authorization', 'Bearer ' + cfg['api_key'])
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode('utf-8'))['choices'][0]['message']['content']


def parse_json_block(content):
    try:
        start, end = content.index('{'), content.rindex('}') + 1
        return json.loads(content[start:end])
    except Exception as e:
        print(f'JSON 解析失败: {e}')
        print('LLM 原始输出片段:', content[:300])
        return None


def step_fetch_api():
    """① API 模式：直接拉官方裸接口（分页取全量），返回 {event, extracted}"""
    hdr = {'User-Agent': 'Mozilla/5.0'}
    # 活动信息
    event = {}
    try:
        with urllib.request.urlopen(urllib.request.Request(API_PROJECT_CONFIG, headers=hdr), timeout=30) as resp:
            cfg = json.loads(resp.read().decode('utf-8'))
            event = {'title': cfg.get('title', '香港餐厅周'), 'source': 'diningcity.asia'}
            end = cfg.get('end_date') or cfg.get('ended_at') or ''
            if end:
                event['validUntil'] = str(end)[:10]
    except Exception as e:
        print(f'  活动配置获取失败（用默认日期）: {e}')

    restaurants = []
    for page in range(1, 6):
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(API_RESTAURANTS.format(page=page), headers=hdr), timeout=30) as resp:
                batch = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f'  第 {page} 页失败: {e}')
            break
        if not batch:
            break
        for r in batch:
            name = (r.get('name') or '').strip()
            if not name:
                continue
            district = (r.get('location') or {}).get('name', '')
            addr = (r.get('address') or '').strip()
            restaurants.append({
                'name': name,
                'addr': (district + ' ' + addr).strip(),
                'meal': '午晚市',
                'price': 0,  # 接口没有餐厅周档位价，如实留空（徽章不显示价格）
                'level': r.get('price_class', ''),
            })
        print(f'  第 {page} 页：{len(batch)} 条（累计有效 {len(restaurants)} 家）')
        if len(batch) < 100:
            break
    return {'event': event, 'extracted': restaurants}


def step_fetch():
    """① 抓网页：返回纯文本（HTML 标签已剥离）"""
    for url in SOURCE_URLS:
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
        except Exception as e:
            print(f'  ✗ {url} 抓取失败: {e}')
            continue
        # 剥标签：script/style 整块删掉，其余标签换空格
        html = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', html, flags=re.S | re.I)
        text = re.sub(r'<[^>]+>', ' ', html)
        text = re.sub(r'\s+', ' ', text)
        if len(text) > 2000:  # 有实质内容
            print(f'  ✓ {url} 抓到 {len(text)} 字符')
            return text
        print(f'  ✗ {url} 内容过少（可能是 JS 渲染的空壳，数据在接口里），换下一个')
    return None


def step_extract(text, cfg):
    """② LLM 抽取结构化数据"""
    prompt = (
        '从下面的网页文本中提取「香港餐厅周」参与餐厅的信息。\n'
        '网页文本（含导航噪音，只关注餐厅列表部分）：\n'
        f'{text[:28000]}\n\n'
        '输出 JSON：'
        '{"event": {"title": "活动名称", "validUntil": "YYYY-MM-DD", "source": "来源网站"}, '
        '"restaurants": [{"name": "餐厅名", "meal": "午市/晚市/午晚市", "price": 价格数字（无价格则 0）, "level": "档次，如 Elite/级别A/级别B/无"}]}\n'
        '规则：名字用中文全称；只输出 JSON，最多 150 家。'
    )
    content = call_llm(prompt, cfg)
    data = parse_json_block(content)
    return data


def step_match(extracted, db_list, cfg):
    """③ LLM 把餐厅周名单映射到本地数据库（分块处理，防输出超长）"""
    db_pairs = [{'id': r['id'], 'name': r['name']} for r in db_list]
    mapping = {}
    for i in range(0, len(extracted), 50):
        chunk = extracted[i:i + 50]
        rw_names = [r['name'] for r in chunk]
        prompt = (
            '把「餐厅周名单」中的每家餐厅对应到「数据库名单」里（同名或同一家店）。\n'
            f'餐厅周名单：{json.dumps(rw_names, ensure_ascii=False)}\n'
            f'数据库名单：{json.dumps(db_pairs, ensure_ascii=False)}\n'
            '输出 JSON：{"mapping": [{"rw_name": "...", "db_id": 数字或 null}]}。'
            'db_id 为 null 表示数据库里没有这家。只输出 JSON。'
        )
        content = call_llm(prompt, cfg)
        data = parse_json_block(content)
        if not data:
            print(f'  匹配块 {i // 50 + 1} 解析失败，跳过')
            continue
        for m in data.get('mapping', []):
            if m.get('db_id') is not None:
                mapping[m['rw_name']] = m['db_id']
        print(f'  匹配块 {i // 50 + 1} 完成')
    return mapping


DISTRICT_DIST = {
    '港大': '400m', '西环': '700m', '石塘咀': '700m', '西营盘': '1000m', '坚尼地城': '1300m',
    '上环': '2500m', '中环': '3000m', '金钟': '4000m', '湾仔': '4000m', '跑马地': '5000m',
    '铜锣湾': '5500m', '尖沙咀': '7000m', '佐敦': '7500m', '油麻地': '8000m', '旺角': '7500m',
    '深水埗': '8000m', '九龙城': '9500m'
}


def step_enrich(unmatched, majors, subs, cfg):
    """⑤ 未匹配的餐厅：LLM 补全字段（分块处理），准备作为新餐厅入库"""
    if not unmatched:
        return []
    out = []
    # 每块 40 家，防单次输出超长
    for i in range(0, len(unmatched), 40):
        chunk = unmatched[i:i + 40]
        prompt = (
            '以下是香港餐厅周参与但本地数据库没有的餐厅。请为每家补全餐厅档案。\n'
            f'餐厅名单（name=餐厅名, addr=已知地址, level=价格档）：'
            f'{json.dumps(chunk, ensure_ascii=False)}\n'
            f'菜系大类（tags 只能从这里选 1-2 个）：{majors}\n'
            f'子类（sub 从这里选 1 个，拿不准选最接近的）：{subs}\n'
            '输出 JSON：{"restaurants": [{"name": "...", "cuisine": "菜系标签", "tags": [...], '
            '"sub": "...", "desc": "一句话介绍（30字内）", '
            '"dishes": ["招牌菜1", "招牌菜2"], "price": 人均价格数字（港币，估算）, '
            '"icon": "一个 emoji", "book": true或false}]\n'
            '规则：addr 沿用已知地址不要改；每家都要输出；只输出 JSON。'
        )
        content = call_llm(prompt, cfg)
        data = parse_json_block(content)
        if not data:
            continue
        for r in data.get('restaurants', []):
            name = r.get('name', '')
            rw = next((x for x in unmatched if x['name'] == name), None)
            addr = (rw or {}).get('addr') or r.get('addr', '香港')
            district = next((d for d in DISTRICT_DIST if d in addr), None)
            out.append({
                'name': name,
                'cuisine': r.get('cuisine', '餐厅'),
                'icon': r.get('icon', '🍽️'),
                'price': 'HK$' + str(r.get('price') or 200),
                'dist': DISTRICT_DIST.get(district, '4000m'),
                'tags': [t for t in r.get('tags', []) if t in majors] or ['中式正餐'],
                'sub': r.get('sub', '') if r.get('sub') in subs else '',
                'addr': addr,
                'desc': r.get('desc', '')[:60],
                'dishes': (r.get('dishes') or [])[:3],
                'book': bool(r.get('book')),
                'rw': rw or {}
            })
    return out


def step_write(event, extracted, mapping, enriched, next_id):
    """④⑤ 写回：已在库的写 promo 字段；不在库的新餐厅直接入库（含 promo）"""
    today = date.today().isoformat()
    lines = RESTAURANTS_PATH.read_text(encoding='utf-8').split('\n')
    # 先清除旧的 promo 字段（幂等覆盖）
    lines = [re.sub(r'\s*"promo": \{.*?\},', '', l) for l in lines]
    # 建 id → promo 映射（已匹配的）
    promos = {}
    for r in extracted:
        db_id = mapping.get(r['name'])
        if not db_id:
            continue
        promos[db_id] = {
            'title': event.get('title', '餐厅周'),
            'meal': r.get('meal') or '午晚市',
            'price': r.get('price') or 0,
            'validUntil': event.get('validUntil') or '2026-09-13',  # 夏季餐厅周已知结束日期
            'source': event.get('source', 'diningcity.hk')
        }
    inserted = 0
    for i, line in enumerate(lines):
        if i == 0:
            continue
        for db_id, promo in promos.items():
            if line.strip().startswith('{ "id":%d,' % db_id):
                target = lines[i + 1]
                if '"closed":false' in target:
                    lines[i + 1] = target.replace(
                        '"closed":false',
                        '"promo": %s, "closed":false' % json.dumps(promo, ensure_ascii=False))
                    inserted += 1
    RESTAURANTS_PATH.write_text('\n'.join(lines), encoding='utf-8')
    print(f'  已写入 {inserted} 家已有餐厅的 promo 字段')

    # 新餐厅入库：在 closing ] 之前插入条目
    new_entries = []
    new_id = next_id
    for r in enriched:
        rw = r.get('rw') or {}
        promo = {
            'title': event.get('title', '餐厅周'),
            'meal': rw.get('meal') or '午晚市',
            'price': rw.get('price') or 0,
            'validUntil': event.get('validUntil') or '2026-09-13',
            'source': event.get('source', 'diningcity.hk')
        }
        sub_field = f'"sub":"{r["sub"]}", ' if r['sub'] else ''
        new_entries.append(
            f'    {{ "id":{new_id}, "name":"{r["name"]}", "cuisine":"{r["cuisine"]}", '
            f'"icon":"{r["icon"]}", "price":"{r["price"]}", "dist":"{r["dist"]}",\n'
            f'      "tags":{json.dumps(r["tags"], ensure_ascii=False)}, {sub_field}'
            f'"promo": {json.dumps(promo, ensure_ascii=False)}, "book":{str(r["book"]).lower()}, "closed":false,\n'
            f'      "addr":"{r["addr"]}", "desc":"{r["desc"]}",\n'
            f'      "dishes":{json.dumps(r["dishes"], ensure_ascii=False)} }}'
        )
        new_id += 1
    if new_entries:
        # 去掉结尾的 '  ]' 和 '}' 两行，插入新条目后再补回
        # 注意：JSON 数组元素之间必须用逗号分隔
        lines = lines[:-2]
        lines.append(',\n' + ',\n'.join(new_entries))
        lines.append('  ]')
        lines.append('}')
        RESTAURANTS_PATH.write_text('\n'.join(lines), encoding='utf-8')
        print(f'  新入库 {len(new_entries)} 家餐厅（含 promo）')


def main():
    cfg = load_cfg()
    if not cfg.get('api_key'):
        print('未配置 LLM API Key')
        return

    print('① 抓取数据（官方 API）...')
    result = step_fetch_api()
    if not result.get('extracted'):
        print('  API 无数据，回退网页抓取...')
        text = step_fetch()
        if not text:
            print('\n❌ 官方页面无法直接抓取（可能是 JS 渲染的空壳）。')
            print('   备选方案：浏览器打开 restaurantweek.diningcity.hk，把餐厅列表复制粘贴到')
            print('   本目录的 rw_manual.txt，再运行 python fetch_restaurant_week.py --manual')
            return
        data = step_extract(text, cfg)
        if not data:
            return
        result = data
    event = result.get('event', {})
    extracted = result.get('extracted', [])
    print(f'  活动: {event.get("title", "")} | 有效至 {event.get("validUntil", "")}')
    print(f'  共 {len(extracted)} 家参与餐厅')

    print('③ LLM 模糊匹配本地数据库...')
    db_data = json.loads(RESTAURANTS_PATH.read_text(encoding='utf-8'))
    mapping = step_match(extracted, db_data['restaurants'], cfg)
    unmatched = [r for r in extracted if r['name'] not in mapping]
    print(f'  成功匹配 {len(mapping)} 家，未匹配 {len(unmatched)} 家')
    next_id = max(r['id'] for r in db_data['restaurants']) + 1
    majors = sorted({t for r in db_data['restaurants'] for t in r['tags']})
    subs = sorted({r['sub'] for r in db_data['restaurants'] if r.get('sub')})

    enriched = []
    if unmatched:
        print('⑤ LLM 补全未匹配餐厅档案（准备入库）...')
        enriched = step_enrich(unmatched, majors, subs, cfg)
        print(f'  补全 {len(enriched)} 家')

    print('④⑤ 写入 restaurants.json...')
    step_write(event, extracted, mapping, enriched, next_id)


def manual_mode():
    """备选：从粘贴的文本文件抽取"""
    cfg = load_cfg()
    manual = ROOT / 'server' / 'rw_manual.txt'
    if not manual.exists():
        print('请把餐厅列表文本粘贴到 server/rw_manual.txt')
        return
    text = manual.read_text(encoding='utf-8')
    print('② LLM 抽取结构化数据（手动文本）...')
    data = step_extract(text, cfg)
    if not data:
        return
    event = data.get('event', {})
    extracted = data.get('restaurants', [])
    print(f'  抽取到 {len(extracted)} 家参与餐厅')
    db_data = json.loads(RESTAURANTS_PATH.read_text(encoding='utf-8'))
    mapping = step_match(extracted, db_data['restaurants'], cfg)
    unmatched = [r for r in extracted if r['name'] not in mapping]
    print(f'  成功匹配 {len(mapping)} 家，未匹配 {len(unmatched)} 家')
    next_id = max(r['id'] for r in db_data['restaurants']) + 1
    majors = sorted({t for r in db_data['restaurants'] for t in r['tags']})
    subs = sorted({r['sub'] for r in db_data['restaurants'] if r.get('sub')})

    enriched = []
    if unmatched:
        print('⑤ LLM 补全未匹配餐厅档案（准备入库）...')
        enriched = step_enrich(unmatched, majors, subs, next_id, cfg)
        print(f'  补全 {len(enriched)} 家')

    print('④⑤ 写入 restaurants.json...')
    step_write(event, extracted, mapping, enriched, next_id)


if __name__ == '__main__':
    if '--manual' in sys.argv:
        manual_mode()
    else:
        main()
