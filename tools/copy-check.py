#!/usr/bin/env python3
"""HanFace 文案違規詞檢查工具

用途：在文案上線前掃描違規用語。針對「非醫療機構的美容業者」設計，
依據台灣化粧品衛生安全管理法、醫療法、公平交易法。

用法：
    python3 tools/copy-check.py <檔案或目錄> [...]
    python3 tools/copy-check.py --list          # 印出可用的安全詞彙
    cat draft.txt | python3 tools/copy-check.py -

跳過檢查（用於引用法條或討論禁用詞的文件）：
    在文件任意位置寫 copycheck:off 開始跳過，copycheck:on 恢復檢查。
    在前 30 行內寫 copycheck:ignore-file 則整份跳過。
    Markdown 用 <!-- copycheck:off -->，Python/JS 用註解，純文字直接寫。

離開代碼：發現高風險（HIGH）項目時回傳 1，否則回傳 0。可接進 CI。

⚠️ 這是輔助工具，不是法律意見。法規的認定方法是「整體綜合表現評價」，
不是逐字比對關鍵字 —— 即使這個工具全綠，整體視覺與敘事若傳達醫療效果
仍可能違規。文案定稿前請送當地衛生局法規諮詢窗口或律師確認。
"""

import sys
import os
import re
import argparse

# ---------------------------------------------------------------------------
# 違規詞庫
# 每項：(詞, 建議替代或說明)
# ---------------------------------------------------------------------------

RULES = [
    {
        "id": "MEDICAL_EFFECT",
        "severity": "HIGH",
        "law": "化粧品衛生安全管理法第10條第2項（醫療效能）",
        "penalty": "60 萬～500 萬元",
        "note": "這是罰則最重的一類，不要冒險。",
        "terms": [
            ("治療", "改用「保養」「護理」「調理」"),
            ("治癒", "刪除，無安全替代"),
            ("痊癒", "刪除，無安全替代"),
            ("根治", "刪除，同時觸犯絕對化用語"),
            ("換膚", "改用「酸類保養」「角質更新護膚」"),
            ("煥膚", "同「換膚」。官方違規例示用的是「換膚」，但同音替代字幾乎"
                     "不可能規避 —— 認定標準是整體綜合表現評價，不是逐字比對。"
                     "改用「酸類保養」「角質更新護膚」"),
            ("去疤", "刪除，無安全替代"),
            ("除疤", "刪除，無安全替代"),
            ("痘疤", "刪除。可改為描述客人的困擾類型而不承諾處理結果"),
            ("疤痕", "刪除，無安全替代"),
            ("淡斑", "改用「亮白膚色」「淨白」"),
            ("除斑", "改用「亮白膚色」「淨白」"),
            ("去斑", "改用「亮白膚色」「淨白」"),
            ("斑點", "刪除，「斑」屬醫療範疇"),
            ("黑斑", "刪除"),
            ("肝斑", "刪除，屬疾病名稱"),
            ("曬斑", "刪除"),
            ("老人斑", "刪除，明確違規例示"),
            ("妊娠紋", "刪除，明確違規例示"),
            ("消除黑眼圈", "刪除，明確違規例示"),
            ("黑眼圈", "高風險，建議刪除"),
            ("真皮層", "刪除，涉及改變身體結構"),
            ("膠原蛋白新生", "改用「賦活肌膚」「活化」"),
            ("促進膠原", "改用「賦活肌膚」「活化」"),
            ("生髮", "刪除，明確違規例示"),
            ("毛髮生長", "刪除，明確違規例示"),
            ("拉提", "改用「緊緻」「緊實」"),
            ("V臉", "刪除，涉及外科手術效果"),
            ("V顏", "刪除，涉及外科手術效果"),
            ("塑臉", "刪除，涉及外科手術效果"),
            ("塑顏", "刪除，涉及外科手術效果"),
            ("抗老", "改用「抗皺緊實」"),
            ("逆齡", "改用「抗皺緊實」"),
            ("除皺", "改用「抗皺緊實」"),
            ("消炎", "刪除，屬藥理作用"),
            ("抗炎", "刪除，屬藥理作用"),
            ("殺菌", "刪除，屬藥理作用"),
            ("抑制發炎", "刪除，屬藥理作用"),
            ("痤瘡", "刪除，屬疾病名稱"),
            ("濕疹", "刪除，屬疾病名稱"),
            ("異位性皮膚炎", "刪除，屬疾病名稱"),
            ("毛孔閉合", "改用「使肌膚光滑」"),
            ("縮小毛孔", "改用「使肌膚光滑」「調理肌膚」"),
        ],
    },
    {
        "id": "MEDICAL_ANALOGY",
        "severity": "HIGH",
        "law": "醫療法第84條＋第87條（比附醫療，非醫療機構不得為醫療廣告）",
        "penalty": "5 萬～25 萬元，且可能同時觸犯化粧品法第10條第2項",
        "note": "這一類沒有安全的改寫方式，只能整句刪除。",
        "terms": [
            ("媲美醫美", "整句刪除"),
            ("醫美級", "整句刪除"),
            ("診所級", "整句刪除"),
            ("醫療級", "整句刪除"),
            ("醫美等級", "整句刪除"),
            ("無痛雷射", "整句刪除"),
            ("類雷射", "整句刪除"),
            ("微整", "整句刪除"),
            ("醫師研發", "整句刪除"),
            ("醫師推薦", "整句刪除"),
            ("專業醫師", "整句刪除"),
            ("醫護團隊", "整句刪除"),
            ("我們的醫師", "整句刪除"),
            ("駐店醫師", "整句刪除"),
        ],
    },
    {
        "id": "MEDICAL_HINT",
        "severity": "HIGH",
        "law": "醫療法第87條（廣告內容暗示或影射醫療業務者視為醫療廣告）",
        "penalty": "5 萬～25 萬元",
        "note": "「療」字直接指向治療。全站取代是成本最低、效益最高的一個修正。",
        "terms": [
            ("療程", "改用「課程」「保養方案」「護膚服務」"),
            ("療法", "改用「保養方式」"),
            ("療程師", "改用「美容師」「護膚顧問」"),
            ("治療師", "改用「美容師」「護膚顧問」"),
            ("診斷", "改用「判斷」「評估」「觀察」"),
            ("問診", "改用「諮詢」「膚況確認」"),
            ("適應症", "改用「適合的膚況」"),
            ("病灶", "刪除"),
            ("術前", "改用「保養前」"),
            ("術後", "改用「保養後」"),
        ],
    },
    {
        "id": "ABSOLUTE",
        "severity": "HIGH",
        "law": "公平交易法第21條（虛偽不實或引人錯誤）＋化粧品法第10條第1項（虛偽誇大）",
        "penalty": "化粧品法 4 萬～20 萬元；公平交易法依第42條處理",
        "note": "「引人錯誤」的認定不論是否與事實相符，只要有引起誤認之虞就算。",
        "terms": [
            ("永久", "改用「持續」「長期」"),
            ("100%", "刪除"),
            ("百分之百", "刪除"),
            ("絕對", "刪除"),
            ("最有效", "刪除"),
            ("效果最好", "刪除"),
            ("全台第一", "刪除"),
            ("全國第一", "刪除"),
            ("業界第一", "刪除"),
            ("唯一", "刪除（除非有可驗證的客觀事實）"),
            ("首例", "刪除"),
            ("保證有效", "刪除"),
            ("無副作用", "刪除"),
            ("零風險", "刪除"),
            ("立即見效", "刪除"),
            ("馬上見效", "刪除"),
            ("一次見效", "刪除"),
            ("完全消除", "刪除"),
            ("永不復發", "刪除"),
        ],
    },
    {
        "id": "CAUTION",
        "severity": "WARN",
        "law": "需人工判斷，視上下文與整體表現而定",
        "penalty": "依認定結果",
        "note": "這些詞不一定違規，但要看它旁邊接了什麼。請逐一人工確認。",
        "terms": [
            ("改善", "單獨使用尚可，但「改善痘疤」「改善斑點」即違規。檢查後面接什麼"),
            ("效果", "避免承諾具體效果。注意「效果擔保」未達成須全額退費的法規風險"),
            ("見效", "高度建議刪除"),
            ("有效", "避免使用"),
            ("修復", "改用「修護」（修護是可用詞，修復偏醫療）"),
            ("美白", "屬特定用途化粧品範疇，若指產品需完成登錄。服務文案建議改用「亮白膚色」"),
            ("抗菌", "偏藥理作用，建議刪除"),
            ("前後對比", "非醫療機構放前後對比照風險極高，建議完全不放"),
            ("before", "檢查是否為前後對比照"),
            ("皮膚科醫師",
             "看用法。「請先去看皮膚科醫師」這種**把客人轉介出去**的寫法是安全的，"
             "而且是遇到疑似皮膚疾病時最正確的處理；但「皮膚科醫師推薦」「與皮膚科"
             "醫師合作」這種攀附或宣稱關係的寫法屬醫療影射，必須刪除"),
            ("就醫", "轉介客人就醫是安全且正確的。但避免自己判斷病名，"
                     "改用「這個狀況建議先讓醫師看過」而不是說出疾病名稱"),
            ("敏感肌", "描述膚況尚可，但不可承諾「治療敏感肌」"),
            ("敏弱", "描述膚況狀態尚可（定型化契約本來就要求詢問是否為敏感性肌膚），"
                     "但「敏弱肌」若被讀成疾病狀態就進入醫療範圍。服務名稱與 hashtag "
                     "建議改用狀態描述，例如「近期肌膚感受度較高」"),
            ("救星", "「救」暗示疾病處理，建議刪除"),
            ("問題肌", "「問題」暗示病狀，建議改用狀態描述"),
            ("痘痘", "描述困擾尚可，但不可承諾處理結果"),
            ("粉刺", "描述困擾尚可，搭配「深層清潔」較安全"),
            ("免費", "注意是否構成優惠促銷宣傳"),
            ("限時", "建議改用「名額限定」，避免假急迫且需持續改期"),
        ],
    },
]

SAFE_VOCAB = {
    "清潔與角質（酸類保養最安全的說法）": [
        "清潔肌膚", "深層清潔", "去除髒汙", "去角質",
        "促進角質更新代謝", "去除老廢角質", "調理肌膚",
    ],
    "膚況與觸感": [
        "滋潤肌膚", "潤澤", "滋養", "保濕", "使肌膚光滑",
        "柔嫩", "水嫩", "柔軟", "潔淨", "展現肌膚自然光澤",
        "促進肌膚新陳代謝",
    ],
    "膚色與緊緻": [
        "淨白", "亮白膚色", "緊緻", "緊實", "緊膚", "抗皺緊實",
        "修護", "呵護", "防護", "保護", "活化", "賦活", "安撫", "舒緩",
    ],
    "臺北市衛生局明列美容服務業得刊登項目": [
        "保濕護膚", "亮白膚色", "臉部保養", "緊膚",
        "亮白更新", "深層清潔", "抗皺緊實", "美白保養",
    ],
}

SEV_ORDER = {"HIGH": 0, "WARN": 1}
TEXT_EXT = {".md", ".txt", ".html", ".htm", ".json", ".yml", ".yaml", ".csv"}

# 詞在別的詞裡面出現時不算（避免誤報）
# 例如「修護」含「修」但不含「修復」；這裡處理的是真正的子字串誤報
# 否定語氣：緊接在禁用詞之前出現這些字時，很可能是免責聲明或
# 「不要這樣寫」的說明，而不是宣稱。降級為需人工確認，不直接判高風險。
NEGATION_MARKERS = [
    "不", "未", "沒", "非", "無", "別", "勿", "禁", "避免", "杜絕",
    "❌", "不可", "不得", "不能", "不會", "不做", "不提供", "不宣稱",
    "絕不", "從不", "並非", "而非", "不是",
]
NEGATION_WINDOW = 6  # 往前看幾個字元

# 假朋友：這些詞含有否定字，但它們是名詞或時間詞，不是在否定後面的字。
# 例如「未使用次數永久有效」的「未」屬於「未使用」，不是在否定「永久」。
# 這類誤判會讓真正的違規被降級，是最危險的方向，必須排除。
NEGATION_FALSE_FRIENDS = [
    "未使用", "未消費", "未提領", "未滿", "未成年", "未來",
    "沒有使用", "不動產", "無論", "無障礙", "無關",
]
# 否定語氣不跨句讀。「請避免曝曬。立即見效」的「避免」否定的是曝曬，不是見效。
CLAUSE_BREAK = "。！？；，、：\n.!?;,:()（）「」『』【】<>《》|"


def negation_before(line, pos):
    """判斷禁用詞前面是否有否定語氣，且不跨越句讀。"""
    window = line[max(0, pos - NEGATION_WINDOW):pos]
    # 只保留最後一個句讀之後的片段
    cut = -1
    for ch in CLAUSE_BREAK:
        idx = window.rfind(ch)
        if idx > cut:
            cut = idx
    if cut >= 0:
        window = window[cut + 1:]
    # 先把假朋友從視窗裡拿掉，避免「未使用」的「未」被當成否定語氣
    for friend in NEGATION_FALSE_FRIENDS:
        window = window.replace(friend, "＿" * len(friend))
    return any(m in window for m in NEGATION_MARKERS)

SUPPRESS_IF_INSIDE = {
    "改善": ["改善方向"],
    "效果": ["效果分析", "效果擔保"],
    "修復": ["修復真皮層"],   # 已由 HIGH 規則單獨報出，避免重複
    "唯一": ["唯一無二"],
}


def blank_code_blocks(lines, path):
    """把 HTML 的 <style>／<script> 內容清空（保留行數，讓行號仍然正確）。

    程式碼不是文案 —— CSS 的 height: 100% 不該被當成絕對化用語。
    """
    if not path.lower().endswith((".html", ".htm")):
        return lines
    out = []
    in_code = False
    for line in lines:
        low = line.lower()
        if not in_code:
            if re.search(r"<(style|script)\b", low):
                in_code = True
                # 保留同一行標籤之前的文字內容
                out.append(re.split(r"<(?:style|script)\b", line, maxsplit=1)[0])
                if re.search(r"</(style|script)>", low):
                    in_code = False
                continue
            out.append(line)
        else:
            if re.search(r"</(style|script)>", low):
                in_code = False
                # 保留結束標籤之後的文字內容
                out.append(re.split(r"</(?:style|script)>", line, maxsplit=1)[-1])
            else:
                out.append("")
    return out


def strip_ignored_regions(lines):
    """回傳 (保留檢查的行, 原始行號) 清單，處理 copycheck:off / copycheck:on。"""
    kept = []
    active = True
    for idx, line in enumerate(lines, start=1):
        low = line.lower()
        if "copycheck:off" in low:
            active = False
            continue
        if "copycheck:on" in low:
            active = True
            continue
        if active:
            kept.append((idx, line))
    return kept


def file_is_ignored(lines):
    head = "\n".join(lines[:30]).lower()
    return "copycheck:ignore-file" in head


def scan_text(lines, path):
    findings = []
    if file_is_ignored(lines):
        return findings, True
    lines = blank_code_blocks(lines, path)
    for lineno, line in strip_ignored_regions(lines):
        for rule in RULES:
            for term, advice in rule["terms"]:
                start = 0
                lowered = line.lower() if term.isascii() else line
                needle = term.lower() if term.isascii() else term
                while True:
                    pos = lowered.find(needle, start)
                    if pos < 0:
                        break
                    start = pos + len(needle)
                    # 誤報抑制
                    suppressed = False
                    for bigger in SUPPRESS_IF_INSIDE.get(term, []):
                        if bigger in line:
                            suppressed = True
                            break
                    if suppressed:
                        continue
                    negated = negation_before(line, pos)
                    findings.append({
                        "path": path,
                        "line": lineno,
                        "col": pos + 1,
                        "term": term,
                        "advice": advice,
                        "rule": rule,
                        "negated": negated,
                        "excerpt": line.strip()[:110],
                    })
    return findings, False


def collect_files(targets):
    out = []
    for t in targets:
        if t == "-":
            out.append("-")
        elif os.path.isdir(t):
            for root, dirs, files in os.walk(t):
                dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
                for f in sorted(files):
                    if os.path.splitext(f)[1].lower() in TEXT_EXT:
                        out.append(os.path.join(root, f))
        elif os.path.isfile(t):
            out.append(t)
        else:
            print("找不到：%s" % t, file=sys.stderr)
    return out


def print_safe_vocab():
    print("=" * 70)
    print("可以使用的安全詞彙（現行《化粧品標示宣傳廣告涉及虛偽誇大或醫療效能")
    print("認定準則》附件二／附件三方向，以及臺北市衛生局明列項目）")
    print("=" * 70)
    for group, words in SAFE_VOCAB.items():
        print("\n【%s】" % group)
        line = "  "
        for w in words:
            if len(line) + len(w) * 2 > 68:
                print(line)
                line = "  "
            line += w + "、"
        print(line.rstrip("、"))
    print("\n" + "-" * 70)
    print("⚠️ 這些是「例示」，不是保證。認定方法是整體綜合表現評價 ——")
    print("   即使每個字都在這張表上，整體敘事若傳達醫療效果仍會違規。")
    print("-" * 70)


def main():
    ap = argparse.ArgumentParser(
        description="HanFace 文案違規詞檢查（非醫療機構美容業者適用）",
        add_help=True,
    )
    ap.add_argument("targets", nargs="*", help="要檢查的檔案或目錄，- 表示標準輸入")
    ap.add_argument("--list", action="store_true", help="印出安全詞彙表後離開")
    ap.add_argument("--warn-only", action="store_true", help="即使有高風險項目也回傳 0")
    ap.add_argument("--quiet", action="store_true", help="只印出統計，不印每一筆")
    args = ap.parse_args()

    if args.list:
        print_safe_vocab()
        return 0

    if not args.targets:
        ap.print_help()
        return 2

    files = collect_files(args.targets)
    all_findings = []
    skipped = []

    for path in files:
        try:
            if path == "-":
                lines = sys.stdin.read().split("\n")
                label = "(stdin)"
            else:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().split("\n")
                label = path
        except OSError as exc:
            print("讀取失敗 %s：%s" % (path, exc), file=sys.stderr)
            continue
        findings, ignored = scan_text(lines, label)
        if ignored:
            skipped.append(label)
        all_findings.extend(findings)

    # 偵測到否定語氣者降級：「我們不做診斷」是免責聲明，不是宣稱
    for f in all_findings:
        f["sev"] = "WARN" if f.get("negated") else f["rule"]["severity"]

    rule_order = {r["id"]: i for i, r in enumerate(RULES)}
    all_findings.sort(key=lambda f: (
        SEV_ORDER[f["sev"]],
        rule_order[f["rule"]["id"]],
        f["path"],
        f["line"],
        f["col"],
    ))

    high = [f for f in all_findings if f["sev"] == "HIGH"]
    warn = [f for f in all_findings if f["sev"] == "WARN"]
    downgraded = [f for f in all_findings if f.get("negated") and f["rule"]["severity"] == "HIGH"]

    if not args.quiet:
        current_key = None
        for f in all_findings:
            key = (f["sev"], f["rule"]["id"])
            if key != current_key:
                current_key = key
                r = f["rule"]
                print()
                print("=" * 70)
                print("[%s] %s" % (f["sev"], r["id"]))
                print("  依據：%s" % r["law"])
                print("  罰則：%s" % r["penalty"])
                print("  說明：%s" % r["note"])
                print("=" * 70)
            flag = "（否定語氣，可能是免責聲明或反例說明）" if f.get("negated") else ""
            print("%s:%d:%d  「%s」%s" % (f["path"], f["line"], f["col"], f["term"], flag))
            print("    建議：%s" % f["advice"])
            print("    原文：%s" % f["excerpt"])

    print()
    print("-" * 70)
    print("掃描 %d 個檔案｜高風險 %d 筆｜需人工確認 %d 筆"
          % (len(files), len(high), len(warn)))
    if downgraded:
        print("其中 %d 筆因偵測到否定語氣而降級（例如「我們不做診斷」屬免責聲明），"
              "仍請人工確認。" % len(downgraded))
    if skipped:
        print("整份跳過（含 copycheck:ignore-file）：%s" % "、".join(skipped))
    if high:
        print()
        print("❌ 有高風險項目，請先修正再上線。")
        print("   最重的一類是醫療效能宣稱，罰 60 萬～500 萬元。")
    elif warn:
        print()
        print("⚠️ 沒有高風險項目，但有需要人工確認的詞。")
    else:
        print()
        print("✅ 未發現違規詞。")
    print()
    print("提醒：法規認定是「整體綜合表現評價」，不是逐字比對。這個工具全綠")
    print("      不等於合規 —— 整體視覺與敘事若傳達醫療效果仍可能違規。")
    print("      定稿前請送當地衛生局法規諮詢窗口或律師確認（衛生局免費）。")
    print("-" * 70)

    if high and not args.warn_only:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # 使用者把輸出接給 head / less 之類的指令時會發生，不是錯誤
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
