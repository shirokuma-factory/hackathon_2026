# Backfull 実装監査記録（2026年11月6日）

空車削減の中心機能は、固定サンプルと架空の便による説明用の段階です。輸送条件の整理、参考額の計算、手入力した見積回答の比較には実装があります。実案件の供給、自社実績の取り込み、当事者間の共有、積載条件と料金の統合が次の改善対象です。

本記録は機能・計算・データ整合性の監査です。顧客獲得、利用者の評価、過去のAIによる点数を根拠にしていません。全件テスト、UI操作、公開操作はしていません。料金計算・便登録を含む既存ソースは変更せず、この新規ファイルだけを作成しました。

## 評価した状態

- 読み取り監査と以下の再現の時点：`b8127fd6aaa4d972a3817a65ed736c76e2c38ac8`。
- 記録作成時：`ca599b254c263998275c199350372da5221b828a`。
- 別セッションによる作業ツリーの変更がありました。コミットだけで状態を特定せず、対象ソースのSHA-256を末尾に記録しています。
- 記録作成時も、再現に使った `spaceb.py`、`loadplan.py`、`estimate.py`、`data/demo/carriers.json` のハッシュは再現時と一致しました。新しいHEADで全件テストをしたという意味ではありません。
- 実行環境はWindowsのPowerShellです。作業ディレクトリは `C:\dev\hackathon_2026`、Pythonは既存の `.venv/Scripts/python.exe` を直接使いました。`-B` でバイトコードの書き込みを止め、予約状態はプロセス内のメモリだけに作りました。

## 優先度と改善後の受入条件

現在の実装範囲でP0は確認していません。P1は空車削減・輸送手配の中心機能を成立させるために必要な事項、P2は判断の正確さを高める事項として付けています。以下の受入条件はAIの提案です。

### 設計上の不足

1. **P1：実案件市場・入札との接続がありません。** `src/`、`tools/`、`web/` のPython・JavaScriptの通信処理を検索しました。地図、OSRM、OSM、e-Stat、JARTICなどの取得はありますが、実際の求荷求車案件の検索・運賃取得・応札処理は見当たりません。便は `src/shipcompare/spaceb.py:78` の架空JSONから読み、`tools/serve_web.py:646` で返します。受入条件は、許可された実案件データの取り込み、または実在の当事者による登録を用意し、情報源・更新時刻・有効期限・連絡先を確認できることです。入札を扱う場合は送信・受領・失効を記録します。

2. **P1：自社実績を取り込む経路がありません。** `web/own.js:27` は固定の `own_sample.json` を読み、`:584` は選択したファイルを読みません。`:599` の帰り便の下書き操作も画面移動だけです。受入条件は、サンプル以外のCSVから元行に対応する実車・空車・不明の区間を作り、訂正後の実績から便を登録できることです。

3. **P1：当事者間の共有と保存がありません。** `tools/serve_web.py:559` は来訪者ごとに別の `Board` を作り、再起動、未使用期間、件数制限で失います。実在の運送会社・荷主・予約所有者の認証もありません。受入条件は、荷主端末の予約を運送会社端末で承諾でき、再起動後も状態が残り、他社の変更を拒否し、同時予約でも容量を超えないことです。

4. **P1：積める車両と料金の条件が統合されていません。** `src/shipcompare/estimate.py:398` は重量だけで車格を残し、`:451` は積載可否を判定しません。運賃表の4tは最大4,000kgですが、`src/shipcompare/loadplan.py:78` の4t公表例は2,700kgです。同じ車格名で別の条件を使います。受入条件は、同じ車両の内寸・最大積載量・装備を使い、載る候補に必要台数と料金を表示し、車両変更で両方が更新されることです。

5. **P2：追加集配後の採算を判断できません。** `src/shipcompare/spaceb.py:635` は標準的運賃に重量・床面積の割合を掛けた参考価格式です。燃料、人件費、高速代、追加走行・荷役時間を積み上げる原価計算ではありません。`src/shipcompare/estimate.py:275` も一部加算を除外します。受入条件は、荷主には追加料金込みの取得済み総額、運送会社には追加売上・追加費用・差額を表示し、不明費用があるときは確定利益を表示しないことです。

6. **P2：大型車の通行可否を判定しません。** `src/shipcompare/routes.py:163` はOSRMの `driving` を使い、`tools/fetch_osrm.py:38` も乗用車経路と明記しています。車高・車幅・総重量に応じた通行制限を入力しません。受入条件は、大型車対応経路を使うか、通行可否が未確認であることを経路・時間・料金に明示し、配車確定の条件と区別することです。

### 具体的な不具合

7. **P1：登録便で総床面積を超える荷物を予約できます。** `src/shipcompare/spaceb.py:733` は `floor_free_m2` を作らず、`:385` も床残量を `None` にします。そのため`:242` の総床面積検査が実行されません。再現1で確認しました。受入条件は、同じ入力を拒否し、予約・辞退・期限切れ後の重量と空間の残量が一致することです。

8. **P1：出発済みの便を予約できます。** `src/shipcompare/spaceb.py:417` は現在時刻を取得しますが、出発時刻を検査しません。再現2で確認しました。受入条件は、出発済みと受付締切後の予約を拒否し、仮予約期限を出発後まで設定できないことです。

9. **P1：途中降ろしの契約下書きの着地が誤ります。** `src/shipcompare/spaceb.py:221` は途中の降ろせる地点を許しますが、`:525` は荷物の着地を使わず、便の終点を記載します。再現3で確認しました。受入条件は、集荷地・着地・日時を予約した荷物の区間から作り、画面・価格式・書面が一致することです。

10. **P2：配置を見つけられない場合を積載不可能と表示します。** `src/shipcompare/loadplan.py:215` の配置手順は複数の列にまたがる残り空間を使いません。`web/loadview.js:16` は判定×を「載りません」と表示します。再現4で確認しました。受入条件は、重量・個別寸法の不適合と配置探索が未解決のケースを区別し、後者を確認が必要と表示することです。

11. **P2：参考額の比較図が確認待ち候補も最安扱いします。** `web/compare_chart.js:19` は金額の有無だけを調べ、`:54` は最安と表示します。積載・利用条件の確認待ちと未算入の追加料金は順位に反映しません。受入条件は、参考額の範囲と確認待ち条件を図中に表示し、受託・積載・総額が確定した見積順位と区別することです。この項目はコードの読み取りによる指摘で、今回UI再現はしていません。

## 再現1：登録便の床面積

前報の20m²と今回の240m²は別入力です。既存の前報 `docs/reviews/2026-11-05_客観レビュー_Codex.md:55` は、100×100×200cm・20kgを20個と記載しています。床面積は1×1×20＝20m²、重量は400kgです。この監査担当が実行した入力は600×200×200cm・1kgを20個です。床面積は6×2×20＝240m²、重量は20kgです。

以下の証拠は今回実行した240m²の入力だけです。20m²の前報を今回の実測結果や採点根拠には使いません。

実行方法は、以下のPythonをPowerShellの文字列変数に入れ、`.venv/Scripts/python.exe -B -c $reviewCode` に渡す方法です。コードは実行した内容を読みやすく改行したものです。

```python
import sys, json
sys.path.insert(0, "src")
from datetime import datetime
from shipcompare import spaceb

base = spaceb.build_registered_trip(dict(
    origin_code="13104", dest_code="28110", vehicle="4t",
    depart_at="2026-11-13T17:00", arrive_at="2026-11-14T10:00",
    weight_slack_kg=2000, unit_footprint_mm=[6000, 2000],
    clear_height_mm=2100, max_piece_kg=300, drop_points=["28110"],
))
base["price_ex_tax_yen"] = 10000
b = spaceb.Board([], clock=lambda: datetime(2026, 11, 5))
t = b.register(base)
s = spaceb.parse_shipment(dict(
    shipment=dict(items=[dict(count=20, length_cm=600, width_cm=200,
                              height_cm=200, weight_kg=1)]),
    dest_code="28110", origin_code="13104",
))
print(json.dumps(dict(
    check="floor", need_m2=float(s.total_floor_m2),
    truck_m2=base["floor_total_m2"], match=b.match_all(s),
    hold=b.hold(t["id"], s),
), ensure_ascii=False))
```

実際の結果（終了コード0）：

```json
{"check": "floor", "need_m2": 240.0, "truck_m2": 13.0, "match": [{"trip_id": "R1", "ok": true, "reasons": []}], "hold": {"hold_id": "H1", "trip_id": "R1", "state": "held", "weight_kg": 20.0, "floor_m2": 0.0, "created_at": "2026-11-05T00:00:00", "expires_at": "2026-11-05T00:30:00"}}
```

1個の高さ2mに対し空き高さは2.1mで、同じ箱の2段積みもできません。それでも容量13m²の便への予約が成立しました。

## 再現2：出発済みの便

次のコードは実際には再現1と同じPythonプロセスで続けて実行しました。以下は独立して動かせるよう、同じ `base` を作る部分を併記したものです。

```python
import sys, json
sys.path.insert(0, "src")
from datetime import datetime
from shipcompare import spaceb

base = spaceb.build_registered_trip(dict(
    origin_code="13104", dest_code="28110", vehicle="4t",
    depart_at="2026-11-13T17:00", arrive_at="2026-11-14T10:00",
    weight_slack_kg=2000, unit_footprint_mm=[6000, 2000],
    clear_height_mm=2100, max_piece_kg=300, drop_points=["28110"],
))
base["price_ex_tax_yen"] = 10000
small = spaceb.parse_shipment(dict(
    shipment=dict(items=[dict(count=1, length_cm=30, width_cm=30,
                              height_cm=30, weight_kg=1)]),
    dest_code="28110",
))
old = dict(base, id="PAST", depart_at="2020-01-01T17:00",
           arrive_at="2020-01-02T10:00")
print(json.dumps(dict(check="past", hold=spaceb.Board(
    [old], clock=lambda: datetime(2026, 11, 5)
).hold("PAST", small)), ensure_ascii=False))
```

実際の結果（終了コード0）：

```json
{"check": "past", "hold": {"hold_id": "H1", "trip_id": "PAST", "state": "held", "weight_kg": 1.0, "floor_m2": 0.0, "created_at": "2026-11-05T00:00:00", "expires_at": "2026-11-05T00:30:00"}}
```

## 再現3：途中降ろしの契約下書き

架空のT7が持つ途中降ろし地点27128（大阪市中央区）に荷物を予約しました。T7の終点は28210（加古川市）です。以下は文字化けを避けるため `ensure_ascii=True` で再実行したコードと出力です。

```python
import sys, json
sys.path.insert(0, "src")
from shipcompare import spaceb
from datetime import datetime

t = next(t for t in spaceb.load_trips() if t["id"] == "T7")
s = spaceb.parse_shipment(dict(
    shipment=dict(items=[dict(count=1, length_cm=30, width_cm=30,
                              height_cm=30, weight_kg=1)]),
    dest_code="27128", origin_code="13108",
))
b = spaceb.Board([t], clock=lambda: datetime(2026, 11, 5))
h = b.hold("T7", s)
b.decide(h["hold_id"], "accept")
c = b.contract(h["hold_id"])
txt = next(i["value"] for i in c["items"] if i["key"] == "transport")
print(json.dumps(dict(
    check="contract_destination", shipment_dest=s.dest_code,
    shipment_destination=spaceb.muni_name(s.dest_code),
    trip_dest=t["dest_code"], trip_destination=spaceb.muni_name(t["dest_code"]),
    transport=txt,
), ensure_ascii=True))
```

実際の結果（終了コード0）：

```json
{"check": "contract_destination", "shipment_dest": "27128", "shipment_destination": "\u5927\u962a\u5e9c\u5927\u962a\u5e02\u4e2d\u592e\u533a", "trip_dest": "28210", "trip_destination": "\u5175\u5eab\u770c\u52a0\u53e4\u5ddd\u5e02", "transport": "\u6771\u4eac\u90fd\u6c5f\u6771\u533a \u304b\u3089 \u5175\u5eab\u770c\u52a0\u53e4\u5ddd\u5e02 \u307e\u3067\u300211/13 17:00 \u767a\u300111/14 12:30 \u7740\u306e\u4e88\u5b9a\u3002\u8377\u7269: 30\u00d730\u00d730cm 1kg \u3092 1 \u70b9\u3002\u5bfe\u4fa1: 43,000\u5186\uff08\u7a0e\u629c\uff09\uff0f1\u4ef6"}
```

`transport` をUnicodeとして読むと「東京都江東区 から 兵庫県加古川市 まで。11/13 17:00 発、11/14 12:30 着の予定。荷物: 30×30×30cm 1kg を 1 点。対価: 43,000円（税抜）／1件」です。予約した大阪市中央区と一致しません。

## 再現4：置ける配置を見つけられない場合

実車を模した値ではなく、配置手順の限界を確認するための仮想荷室です。荷室は4×4×1m、荷物は3×2×1mが2個と4×1×1mが1個です。各1kgで積み重ねは不可にしました。

```python
import sys, json
sys.path.insert(0, "src")
from shipcompare.loadplan import Container, LoadItem, plan_load

c = Container("example", "example", 4000, 4000, 1000, 1000,
              "public", "sample")
r = plan_load([
    LoadItem(2, 300, 200, 100, 1, False),
    LoadItem(1, 400, 100, 100, 1, False),
], c)
print(json.dumps(dict(summary=r["summary"], placements=r["placements"],
                      unplaced=r["unplaced"]), ensure_ascii=True))
```

`basis="public"` はこの実行で使った文字列です。通常の画面の選択肢ではありません。このケースは配置失敗の分岐を通り、`basis` による○・△の選択分岐を通りません。

実際の結果（終了コード0）：

```json
{"summary": {"fits": false, "all_placed": false, "requested": 3, "placed": 2, "unplaced_count": 1, "floor_area_pct": 75.0, "volume_pct": 75.0, "total_weight_kg": 3.0, "payload_kg": 1000, "weight_ok": true, "weight_over_kg": 0.0, "stacked": false, "verdict": "\u00d7", "note": "\u76ee\u5b89\u3067\u3059\u3002\u5b9f\u969b\u306e\u7a4d\u4ed8\u3051\u30fb\u56fa\u7e1b\u30fb\u8377\u5f79\u306f\u904b\u9001\u4f1a\u793e\u306b\u78ba\u8a8d\u3057\u3066\u304f\u3060\u3055\u3044\u3002"}, "placements": [{"order": 0, "item": 0, "nth": 0, "x": 0, "y": 0, "z": 0, "l": 2000, "w": 3000, "h": 1000, "rotated": true}, {"order": 1, "item": 0, "nth": 1, "x": 2000, "y": 0, "z": 0, "l": 2000, "w": 3000, "h": 1000, "rotated": true}], "unplaced": [{"item": 1, "count": 1, "reason": "\u5e8a\u306b\u7a7a\u304d\u304c\u306a\u304f\u3001\u7a4d\u307f\u91cd\u306d\u4e0d\u53ef\u306e\u8377\u3067\u3059"}]}
```

一方、返された2個の箱をそのまま置き、残る箱を `(x,y,z)=(0,3000,0)`、寸法 `(l,w,h)=(4000,1000,1000)` として置けば、3個とも荷室内に収まり、箱同士も重なりません。これは数値上の配置の確認であり、実車の積付け・固縛を確認したという意味ではありません。

## 正しく区別できている点

- 全国荷量差は自社の空車率の実測ではありません。`src/shipcompare/market.py:35` は往復の重量差、`:47` は仮定を置いた参考曲線です。画面にも「便の数ではない」「個々の運送会社の空きではない」と注記があり、この点自体を計算バグとして数えていません。
- `src/shipcompare/quotes.py:101` は不明費目、有効期限切れ、条件変更前の見積を分け、完全な回答だけを順位に入れます。この見積回答の比較と、前述の参考額の比較図は別の処理です。
- `README.md:5` と`:75`以降は、空車分析・帰り便の接続が架空サンプルで、取り込み・保存・共同利用が未実装であることを明記しています。現状説明との大きな齟齬は確認していません。

## 対象ソースのSHA-256

以下のパスは `C:\dev\hackathon_2026` からの相対表記です。これは監査対象を特定する記録で、ファイルの安全性や計算の正しさをハッシュで証明するものではありません。

```text
README.md                         9EA441A923935275BBA06D6C3C6706B2B368D7E5563AB04E12320EAE0C6410B8
SPEC.md                           DE0DA349DC8A9FF327333A9CB324814E89E0B3B28A258728924B2697562DFEA1
src/shipcompare/market.py          5023A2038E24F75933FABE7ECEB88C922BC4A4195960C3F601BB69969A755CF8
src/shipcompare/spaceb.py          3450FA7588C3578C776B43498C4418F95E852EB3F596B45E2FBB9C24DE59807B
src/shipcompare/estimate.py        C9BF324DF7C83378596EB4806A65891B3F3E707DA187CB751457F51E7B871C60
src/shipcompare/loadplan.py        3D51345349AB31C7469FF69A51A52E79487A24F116B7143EE9E5549B5E7CB129
web/own.js                        E17A02FB596CD6715B0597C6E347B40EB57F84D843578ABC974EAA68B8126D49
tools/serve_web.py                88B238B578FAB206796177AEB972C43B858AEA98816827A0BBDE70C8A4EFBFE7
web/market.js                     A7CF33FFC8BF5566005BE13068D8D12548F3C11DFC0D35218EF3563E9B70E5CA
web/compare_chart.js              3647FA71D039BA4C7540EF818FFE24DB5B4BFA80DE378024CB354DC45751B7F6
web/loadview.js                   263B643D75DDEA1388AB6CE7F1703B89E3CDC91945F3FC6406DF8F13646FB130
data/demo/carriers.json           4C2273A4B5680653A2365BF63B4111EBD98A46F171B1E88BE86867C6995ED199
```
