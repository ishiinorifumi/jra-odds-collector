"""オッズ表示ページのHTMLから、実際のオッズ数値を抽出する。"""
from bs4 import BeautifulSoup


def parse_tanpuku(html):
    """単勝・複勝オッズ（馬番順）ページを解析する。
    戻り値: [{"umaban": "1", "horse_name": "...", "tan_odds": "85.1",
              "fuku_min": "7.1", "fuku_max": "12.0", "weight": "490", ...}, ...]
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tanpuku")
    if table is None:
        return None

    rows = []
    for tr in table.select("tbody tr"):
        umaban = tr.select_one("td.num")
        horse = tr.select_one("td.horse")
        tan = tr.select_one("td.odds_tan")
        fuku = tr.select_one("td.odds_fuku")
        age = tr.select_one("td.age")
        weight = tr.select_one("td.h_weight")
        kinryo = tr.select_one("td.weight")
        jockey = tr.select_one("td.jockey")
        trainer = tr.select_one("td.trainer")

        if umaban is None:
            continue

        fuku_min = fuku_max = None
        if fuku is not None:
            mn = fuku.select_one("span.min")
            mx = fuku.select_one("span.max")
            fuku_min = mn.get_text(strip=True) if mn else None
            fuku_max = mx.get_text(strip=True) if mx else None

        weight_val = weight_diff = None
        if weight is not None:
            full_text = weight.get_text(" ", strip=True)
            parts = full_text.split()
            weight_val = parts[0] if parts else None
            weight_diff = parts[1].strip("()") if len(parts) > 1 else None

        rows.append({
            "umaban": umaban.get_text(strip=True),
            "horse_name": horse.get_text(strip=True) if horse else None,
            "tan_odds": tan.get_text(strip=True) if tan else None,
            "fuku_min": fuku_min,
            "fuku_max": fuku_max,
            "age": age.get_text(strip=True) if age else None,
            "weight": weight_val,
            "weight_diff": weight_diff,
            "kinryo": kinryo.get_text(strip=True) if kinryo else None,
            "jockey": jockey.get_text(strip=True) if jockey else None,
            "trainer": trainer.get_text(strip=True) if trainer else None,
        })
    return rows


def parse_combination_table(html, table_class_hint=None):
    """馬連・ワイド・馬単・3連複・3連単など、組番×オッズのテーブルを汎用的に解析する。
    構造がベットタイプごとに異なるため、まずは全<table>から
    (行の全セルテキストのリスト)を素直に抜き出す形にとどめる。
    生HTMLは別途保存するため、ここでの解析精度は将来的に個別改善する前提。
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    result = []
    for table in tables:
        cls = table.get("class")
        rows = []
        for tr in table.select("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        result.append({"class": cls, "rows": rows})
    return result


def get_odds_status_label(html):
    """「最終オッズ」「中間オッズ」など、このページのオッズが
    どの段階のものかを示すラベルを抽出する(取れなければ"unknown")。
    発走前ページでの実際の表記は未確認のため、初回稼働時に実データで要検証。"""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text()
    for label in ["最終オッズ", "確定オッズ", "中間オッズ", "発売前", "発売中"]:
        if label in text:
            return label
    return "unknown"
