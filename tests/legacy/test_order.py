"""
Alpaca Paper Trading — 注文テストスクリプト
ManagerAgent が alpaca_trade スキルを呼び出す経路を再現する。
"""

import sys
import json
import importlib.util
from pathlib import Path

# ---- モジュールロード (skills/__init__.py の循環importを回避) ----
def load_skill(name: str):
    path = Path(__file__).parent / "skills" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

alpaca = load_skill("alpaca_trade")

# ------------------------------------------------------------------ #
# 1. 発注前のアカウント状態を確認                                       #
# ------------------------------------------------------------------ #
print("=" * 55)
print("  ManagerAgent — alpaca_trade スキル 連携テスト")
print("=" * 55)

print("\n[1] 発注前アカウント情報")
account_before = alpaca.run("get_account")
print(json.dumps(account_before, indent=2, ensure_ascii=False))

# ------------------------------------------------------------------ #
# 2. 成行注文を発注                                                     #
# ------------------------------------------------------------------ #
ORDER = dict(symbol="AAPL", qty=1, side="buy")

print(f"\n[2] 成行注文を送信: {ORDER}")
try:
    result = alpaca.run("order", **ORDER)
    print("\n  ✔ 注文成功")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    success = True
except Exception as e:
    print(f"\n  ✘ 注文失敗: {e}")
    success = False
    sys.exit(1)

# ------------------------------------------------------------------ #
# 3. 発注後のアカウント状態を確認                                       #
# ------------------------------------------------------------------ #
print("\n[3] 発注後アカウント情報")
account_after = alpaca.run("get_account")
print(json.dumps(account_after, indent=2, ensure_ascii=False))

buying_power_diff = account_after["buying_power"] - account_before["buying_power"]
print(f"\n  Buying Power 変化: {buying_power_diff:+,.2f} USD")

# ------------------------------------------------------------------ #
# 4. サマリー                                                          #
# ------------------------------------------------------------------ #
print("\n" + "=" * 55)
print("  テスト結果サマリー")
print("=" * 55)
print(f"  注文ステータス : {result['status']}")
print(f"  Order ID       : {result['order_id']}")
print(f"  銘柄           : {result['symbol']}")
print(f"  数量           : {result['qty']} 株")
print(f"  売買方向       : {result['side']}")
print(f"  注文タイプ     : {result['type']}")
print(f"  有効期限       : {result['time_in_force']}")
print(f"  送信日時       : {result['submitted_at']}")
print(f"  Paper Trading  : {account_before['paper']}")
print("=" * 55)
