"""Check specification counterexamples, not a production implementation.

Run from this package's parent: python logistics-review-20261103/checks/check_spec_math.py
Uses only standard-library modules. All capacities/prices here are synthetic.
"""
import calendar
import json
import math
import os
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def long_path(p):
    s = os.path.abspath(p)
    return Path('\\\\?\\' + s) if os.name == 'nt' and not s.startswith('\\\\?\\') else Path(s)


def main():
    results = []

    def check(case_id, label, actual, expected, original=None):
        passed = math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6) if isinstance(expected, float) else actual == expected
        item = dict(id=case_id, description=label, actual=actual, expected=expected, passed=passed)
        if original is not None:
            item['original_formula_result'] = original
        results.append(item)

    # Fixed expected values expose the original specification's counterexamples.
    check('M01', '50 pallets need 3 trailers at 24 pallet slots, despite a 20t payload',
          math.ceil(max(50 * 500 / 20000, 50 / 24)), 3,
          math.ceil(50 * 500 / 20000))
    check('M02', '100 pallets need 7 containers at 16 slots; mass-only gives 5',
          math.ceil(max(100 * 500 / 10000, 100 / 16)), 7,
          math.ceil(100 * 500 / 10000))
    check('M03', '100 pallets in four 25-pallet dispatches need 8 vehicle trips',
          sum(math.ceil(25 / 16) for _ in range(4)), 8, math.ceil(100 / 16))
    check('M04', 'A 100000 estimate with +10 percent relative error implies 90909.09 actual',
          100000 / 1.10, 90909.0909090909, 100000 * 0.65)
    check('M05', 'A 100000 estimate with -35 percent relative error implies 153846.15 actual',
          100000 / 0.65, 153846.15384615384, 100000 * 1.10)
    check('M06', 'Intervals [10,20] and [30,40] do not overlap',
          max(10, 30) <= min(20, 40), False, 10 <= 30)
    check('M07', 'Intervals [10,20] and [20,30] touch',
          max(10, 20) <= min(20, 30), True)
    check('M08', 'Four driving hours need zero intermediate rests in the simplified model',
          max(0, math.ceil(4 / 4) - 1), 0, math.floor(4 / 4))
    check('M09', 'Eight driving hours need one intermediate rest in the simplified model',
          max(0, math.ceil(8 / 4) - 1), 1, math.floor(8 / 4))
    check('M10', 'Driving just over four hours needs one intermediate rest',
          max(0, math.ceil(4.001 / 4) - 1), 1)
    check('M11', 'A zero-quantity scenario uses zero vehicles', math.ceil(0 / 16), 0)
    check('M12', 'A one-cubic-metre, 100kg slot: 0.8 cubic metres and 110kg need two slots',
          math.ceil(max(.8 / 1, 110 / 100)), 2)
    check('M13', '1.5 cubic metres and 500kg can sell only one complete fixed slot',
          math.floor(min(1.5 / 1, 500 / 100)), 1)
    check('M14', 'Raw cargo fits but two required slots cannot be reserved in 1.5 cubic metres',
          math.ceil(max(.8 / 1, 110 / 100)) <= math.floor(min(1.5 / 1, 500 / 100)),
          False, .8 <= 1.5 and 110 <= 500)
    check('M15', 'Monday dispatches in November 2026 occur five times, not four',
          sum(calendar.weekday(2026, 11, day) == calendar.MONDAY for day in range(1, 31)), 5, 4)
    check('M16', 'Half yen surcharge uses explicit ceiling to 63 yen',
          int((Decimal('100') / Decimal('4') * Decimal('2.5')).quantize(Decimal('1'), rounding=ROUND_CEILING)), 63)

    try:
        now = datetime.now(ZoneInfo('Asia/Tokyo')).isoformat()
    except ZoneInfoNotFoundError:
        from datetime import timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=9))).isoformat()
    out = dict(kind='specification_math_only', executed_at=now,
               production_tested=False, original_64_tests_reproduced=False,
               total=len(results), passed=sum(x['passed'] for x in results), cases=results)
    dest = long_path(__file__).parent / 'results.json'
    dest.write_bytes((json.dumps(out, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print(f"Specification arithmetic: {out['passed']}/{out['total']} passed")
    print('No production, database, UI or original 64-case tests were run.')
    raise SystemExit(0 if all(x['passed'] for x in results) else 1)


if __name__ == '__main__':
    main()
