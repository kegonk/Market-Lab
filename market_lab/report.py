"""Offline, self-contained HTML and CSV output. No CDN or runtime fetch."""

from __future__ import annotations

import csv
from html import escape
from pathlib import Path


def write_csv(result: dict, output: Path) -> None:
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["case", "tick", "last_price_vcu", "best_bid_vcu",
                         "best_ask_vcu", "spread_bps", "bid_depth_8_units",
                         "ask_depth_8_units", "traded_qty_cumulative"])
        for case in ("baseline", "shock"):
            for row in result[case]["timeline"]:
                writer.writerow([result[case]["case"], row["tick"], row["last_price"],
                                 row["best_bid"], row["best_ask"], row["spread_bps"],
                                 row["bid_depth_8"], row["ask_depth_8"],
                                 row["traded_qty_cumulative"]])


def _polyline(timeline: list[dict], metric: str, lo: float, hi: float,
              color: str, width: int = 980, height: int = 300) -> str:
    points = []
    for i, row in enumerate(timeline):
        value = row[metric]
        if value is None:
            continue
        x = 24 + 930 * i / max(len(timeline) - 1, 1)
        y = 12 + (height - 35) * (hi - value) / max(hi - lo, 0.000001)
        points.append(f"{x:.2f},{y:.2f}")
    return f'<polyline fill="none" stroke="{color}" stroke-width="2.5" points="{" ".join(points)}"/>'


def write_html(result: dict, output: Path) -> None:
    b = result["baseline"]["shock_trade"]
    s = result["shock"]["shock_trade"]
    settings = result["settings"]
    names = ("baseline", "shock")
    prices = [p["last_price"] for n in names for p in result[n]["timeline"]]
    depths = [p["bid_depth_8"] for n in names for p in result[n]["timeline"]]
    def chart(metric: str, limits: list[float]) -> str:
        lo, hi = min(limits), max(limits)
        if hi == lo:
            hi += 1
        if metric == "last_price":
            lo -= 0.2
            hi += 0.2
        shock_x = 24 + 930 * settings["shock_tick"] / (settings["ticks"] - 1)
        segments = ''.join(_polyline(result[n]["timeline"], metric, lo, hi,
                                     "#48a7f0" if n == "baseline" else "#ef9754") for n in names)
        return (f'<svg viewBox="0 0 980 300" role="img" aria-label="{metric}">'
                f'<line x1="{shock_x:.2f}" x2="{shock_x:.2f}" y1="0" y2="300" stroke="#e7d374" stroke-dasharray="6 6"/>'
                f'{segments}<text x="30" y="22" fill="#c5cedd" font-size="13">max {hi:.2f}</text>'
                f'<text x="30" y="283" fill="#c5cedd" font-size="13">min {lo:.2f}</text></svg>')
    delta = result["comparison"]["slippage_increase_bps"]
    html = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kairos Market Lab • LQ-001</title>
<style>body{{font:16px/1.5 system-ui,sans-serif;background:#0c1220;color:#e9edf6;max-width:1060px;margin:auto;padding:30px}}h1{{margin-bottom:0}}p,.sub{{color:#aebbd2}}.pill{{display:inline-block;background:#173348;border:1px solid #42617b;border-radius:12px;padding:5px 12px}}.panel{{padding:18px;background:#141f31;border:1px solid #2e4059;border-radius:12px;margin:18px 0}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}}.stat{{background:#141f31;padding:20px;border-radius:12px;border:1px solid #2e4059}}strong{{font-size:25px}}svg{{width:100%;height:auto}}.legend{{display:flex;gap:25px;flex-wrap:wrap}}.a{{color:#48a7f0}}.b{{color:#ef9754}}code{{overflow-wrap:anywhere;color:#bddaff}}</style></head><body>
<div class="pill">SIMULATED · no real market data</div><h1>Kairos Market Lab</h1><p>Эксперимент LQ-001 · Внезапное снижение ликвидности покупателей</p>
<div class="stats"><div class="stat">Базовое проскальзывание<br><strong>{b["slippage_bps"]} bps</strong></div><div class="stat">После удаления ликвидности<br><strong>{s["slippage_bps"]} bps</strong></div><div class="stat">Разница<br><strong>{delta} bps</strong></div><div class="stat">Отменено виртуальных заявок<br><strong>{s["cancelled_bid_qty"]} units</strong></div></div>
<div class="panel"><h2>Синтетическая цена, VCU</h2>{chart("last_price", prices)}<div class="legend"><span class="a">● Контроль</span><span class="b">● Шок ликвидности</span><span>┊ Момент шока: тик {settings["shock_tick"]}</span></div></div>
<div class="panel"><h2>Объём BUY в восьми лучших уровнях стакана</h2>{chart("bid_depth_8", depths)}<div class="legend"><span class="a">● Контроль</span><span class="b">● Шок ликвидности</span></div></div>
<div class="panel"><h2>Параметры</h2><p>Агентов: {settings["agent_count"]}; seed: {settings["seed"]}; тиков: {settings["ticks"]}; удаляемая доля: {settings["cancel_fraction"]:.0%}; уровней для удаления: {settings["cancel_levels"]}; разовый SELL: {settings["shock_sell_qty"]} виртуальных единиц.</p><p>Идентичные события участников в обоих случаях. Изменяется только наличие заявок в BUY-стакане до контрольной продажи.</p><p>Суммарный SHA-256 входной ленты и параметров: <code>{escape(result["tape_sha256"])}</code></p></div>
<p class="sub">Цены и участники искусственные. Это визуализация эксперимента, а не прогноз рынка, финансовая рекомендация или торговая система.</p></body></html>'''
    output.write_text(html, encoding="utf-8")
