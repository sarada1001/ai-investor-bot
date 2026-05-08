---
title: Trading Dashboard
created: 2026-05-08
---

# Trading Dashboard

> Obsidian **Dataview** プラグインが必要です。  
> `obsidian_logs/` フォルダを Vault に含めてください（例: Vault 直下に `obsidian_logs` をシムリンクまたは配置）。

---

## 最近のトレード一覧

```dataview
TABLE
  date        AS "日付",
  ticker      AS "銘柄",
  action      AS "売買",
  outcome     AS "結果",
  profit_loss AS "損益",
  tags        AS "タグ"
FROM "obsidian_logs"
WHERE date != null
SORT date DESC
```

---

## 勝敗サマリー（outcome 別件数）

```dataview
TABLE rows.file.name AS "ログファイル", length(rows) AS "件数"
FROM "obsidian_logs"
WHERE outcome != null
GROUP BY outcome
SORT length(rows) DESC
```

---

## 銘柄別 エントリー回数

```dataview
TABLE rows.file.name AS "ログファイル", length(rows) AS "件数"
FROM "obsidian_logs"
WHERE ticker != null
GROUP BY ticker
SORT length(rows) DESC
```

---

## 直近30日 — BUY / SELL 件数

```dataview
TABLE rows.file.name AS "ログファイル", length(rows) AS "件数"
FROM "obsidian_logs"
WHERE date >= date(today) - dur(30 days)
GROUP BY action
SORT length(rows) DESC
```

---

## ティッカー Wiki へのクイックリンク

- [[tickers/AAPL|AAPL — Apple]]
- [[tickers/NVDA|NVDA — NVIDIA]]
- [[tickers/NKE|NKE — Nike]]
- [[tickers/GEHC|GEHC — GE HealthCare]]
- [[tickers/HSY|HSY — Hershey]]

---

*自動更新: `server_librarian.py --ingest` を実行するたびに `INDEX.md` が更新されます。*
