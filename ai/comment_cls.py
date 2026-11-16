import re
def classify(t):
    """前検日コメントを (大分類, 小分類) に。"""
    t=(t or '').strip().replace('　','')
    if not t: return ('なし','なし')
    if '補充' in t: return ('補充','補充')
    if re.search(r'ジカ|競り|競る', t): return ('競り','競り')
    if re.search(r'単騎|一人|1人|ひとり|独自|自分だけ', t):
        return ('単騎', '単騎自力' if re.search(r'自力|前|先行|仕掛', t) else '単騎')
    if re.search(r'3番手|三番手|4番手', t): return ('3番手','3番手')
    if re.search(r'勢|ライン', t) and not re.search(r'自力', t): return ('地区勢へ','先手ライン' if '先手' in t else '地区勢へ')
    if re.search(r'君|さん|選手|マーク|目標|へ。?$|番手', t) and not re.search(r'自力', t):
        return ('追走', 'マーク' if 'マーク' in t else ('目標' if '目標' in t else '追走'))
    if re.search(r'先行|逃げ|主導権|前で(頑張|やる|勝負)|駆け|突っ張|カマシ|かまし', t): return ('自力','自力・積極')
    if re.search(r'自力', t):
        if re.search(r'自在', t): return ('自力','自力自在')
        if re.search(r'基本|中心|メイン', t): return ('自力','自力基本')
        if re.search(r'勝負|頑張|やる|しっかり', t): return ('自力','自力・強め')
        return ('自力','自力')
    if re.search(r'決め(ず|ない)|何でも|流れ|臨機|取れた位置|位置から|切れ目|前々|自在|柔軟|展開|成り行き|様子', t):
        sub='前々' if '前々' in t else ('決めず' if re.search(r'決め',t) else ('自在' if '自在' in t else '流れ・位置'))
        return ('自在', sub)
    if re.search(r'自分で|前で|仕掛', t): return ('自力','自力')
    return ('その他','その他')
