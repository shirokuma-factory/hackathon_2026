"""Reproduce geometric scenarios and capacity counterexamples; no safety certification."""
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path


def long_path(p):
    s = os.path.abspath(p)
    return Path('\\\\?\\' + s) if os.name == 'nt' and not s.startswith('\\\\?\\') else Path(s)


TRUCKS = [
    dict(id='ocean_2t', group='2t', length=3000, width=1700, height=1700, payload=2000,
         source='https://www.oceanlinks.co.jp/domestic/truck/'),
    dict(id='tesco_2t', group='2t', length=4200, width=1800, height=2000, payload=2000,
         source='https://www.tescompo.co.jp/business/delivery.html'),
    dict(id='tesco_4t', group='4t', length=6200, width=2300, height=2200, payload=2850,
         source='https://www.tescompo.co.jp/business/delivery.html'),
    dict(id='ocean_4t', group='4t', length=6200, width=2200, height=2400, payload=2700,
         source='https://www.oceanlinks.co.jp/domestic/truck/'),
    dict(id='tesco_10t', group='10t', length=9440, width=2350, height=2565, payload=13000,
         source='https://www.tescompo.co.jp/business/delivery.html'),
    dict(id='ocean_10t', group='10t', length=9600, width=2400, height=2600, payload=13500,
         source='https://www.oceanlinks.co.jp/domestic/truck/'),
]
RBS = [
    dict(id='WKP-1180', width=1100, depth=800, height=1700, tare=45.5,
         source='https://www.wako-pallet.co.jp/archives/product/carry_basket_list1'),
    dict(id='JITBOX', width=1100, depth=1100, height=2000, tare=None,
         source='https://www.jitbox.co.jp/faq/'),
]


def main():
    calculations = []
    ranges = {}
    for rb in RBS:
        for group in ('2t', '4t', '10t'):
            samples = []
            for truck in [t for t in TRUCKS if t['group'] == group]:
                for deduction in (200, 300):
                    raw = max(0, truck['height'] - rb['height'])
                    net = max(0, raw - deduction)
                    row = dict(truck=truck['id'], rb=rb['id'],
                               rb_exceeds_inner_height=rb['height'] > truck['height'],
                               deduction_mm=deduction, raw_height_mm=raw,
                               net_height_mm=net,
                               raw_volume_m3=rb['width'] * rb['depth'] * raw / 1e9,
                               net_volume_m3=rb['width'] * rb['depth'] * net / 1e9)
                    samples.append(row)
                    calculations.append(row)
            ranges[(rb['id'], group)] = {
                key: [min(r[key] for r in samples), max(r[key] for r in samples)]
                for key in ('raw_height_mm', 'net_height_mm', 'raw_volume_m3', 'net_volume_m3')
            }

    cases = []

    def check(label, actual, expected):
        cases.append(dict(description=label, actual=actual, expected=expected, passed=actual == expected))

    expected_heights = {
        ('WKP-1180', '2t'): [0, 100], ('WKP-1180', '4t'): [200, 500],
        ('WKP-1180', '10t'): [565, 700], ('JITBOX', '2t'): [0, 0],
        ('JITBOX', '4t'): [0, 200], ('JITBOX', '10t'): [265, 400],
    }
    for key, expected in expected_heights.items():
        check(f'{key[0]}・{key[1]}の仮の有効高mm', ranges[key]['net_height_mm'], expected)
    check('4t・WKPの控除後投影容積m3', ranges[('WKP-1180', '4t')]['net_volume_m3'], [.176, .44])
    check('10t・WKPの控除後投影容積m3', ranges[('WKP-1180', '10t')]['net_volume_m3'], [.4972, .616])
    check('10t・JITBOXの控除後投影容積m3', ranges[('JITBOX', '10t')]['net_volume_m3'], [.32065, .484])
    check('内高1700に外高2000のRBは入らない', 2000 > 1700, True)
    check('RBの外高と内高の取り違えによる過大評価mm', (2600 - 1700) - (2600 - 2000), 300)
    check('RB10台と各200kgの荷物の残り重量kg', 2850 - 10 * (45.5 + 200), 395.0)
    check('未算入の棚80kgを加えた残り重量kg', 2850 - 10 * (45.5 + 200) - 80, 315.0)
    check('棚1セット300kgで制限した一次重量上限kg', min(315, 300), 300)
    check('各300kgを積んだ下段だけで過積載kg', 10 * (45.5 + 300) - 2850, 605.0)
    check('個別区画各60kgでも共通余力100kgを2件で超過', 60 + 60 > 100, True)
    check('1m3で実重100kgの一般容積換算課金重量kg', max(100, 1 * 280), 280)
    check('容積で入っても最大積載5000kgを超える鉄道貨物', 5100 > 5000, True)

    out = dict(kind='寸法と重量の試算・反例の検算',
               executed_at=datetime.now(timezone(timedelta(hours=9))).isoformat(),
               production_tested=False, vehicle_safety_certified=False,
               assumptions={'deduction_mm': [200, 300], 'lower_cargo_exceeds_rb': False,
                            'range_meaning': '公開例とAIの仮定の組合せ。統計的な範囲ではありません。'},
               trucks=TRUCKS, rollboxes=RBS, calculations=calculations,
               ranges=[dict(rb=k[0], group=k[1], **v) for k, v in ranges.items()],
               total=len(cases), passed=sum(c['passed'] for c in cases), cases=cases)
    dest = long_path(__file__).parent / 'rb_space_results.json'
    dest.write_bytes((json.dumps(out, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print(json.dumps({'total': out['total'], 'passed': out['passed'], 'vehicle_safety_certified': False}))
    raise SystemExit(0 if out['passed'] == out['total'] else 1)


if __name__ == '__main__':
    main()
