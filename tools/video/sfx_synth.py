"""デモ動画用の効果音キットを合成で作る（ダウンロードなし、numpy と標準の wave のみ）。

出力: outputs/video/sfx/kit/ （48 kHz / 24 bit / ステレオ）
キー: D メジャー（D4=293.66 Hz, F#, A）。全部の音を同じ音階・同じ柔らかさでそろえる。
高域は 7〜9 kHz 付近で落とし、鋭いアタックや高音の「ピコッ」を避ける。

音ごとの用途
- whoosh_soft   (0.7 s)  画面切り替え・要素のスライドイン。やわらかい空気の通過音。
- whoosh_line   (1.0 s)  線が左から右へ走るとき。音も L から R へ動き、D の倍音が薄く半音上がる。山は 0.3 s。
- riser_soft    (1.2 s)  章カードの直前。ノイズと正弦波のパッドが上がり、静かに止まって短い余韻。
- pop_a/b/c     (0.25 s) 強調語が着地するとき。D5 / F#5 / A5（主音・3度・5度）の丸い音。
- tick_1..4, tick (0.05 s) 数字のカウントアップ（約 70 ms ごと）。木の「こつ」。極小音量、音高を 4 通りに揺らす。
- land_soft     (0.9 s)  数字が決まる・結果が出る瞬間。低い胸の鼓動 + 空気 + D5 のベル。
- chime_logo    (2.5 s)  ロゴの表示。D メジャーのやわらかいベル和音、長い減衰、ステレオコーラス。
- ui_tap        (0.08 s) 画面録画のカーソルクリック。小さく丸いタップ。
- preview.wav            全部を 0.6 s 間隔で順に並べた試聴用（tick は 15 個を 70 ms 間隔）。
- preview_spectrogram.png  preview.wav のスペクトログラム（高域のきつさ確認用）。

音量のそろえ方: 各音を -6 dBFS にピーク正規化したあと、目標の実効値（有音区間の RMS）に
合わせて下げる。tick は極小、pop / whoosh は中程度。
"""
import wave, zlib, struct
from pathlib import Path
import numpy as np

SR = 48000
D4 = 293.66
D5, FS5, A5 = D4 * 2, 369.99 * 2, 440.0 * 2
OUT = Path(__file__).resolve().parents[2] / "outputs" / "video" / "sfx" / "kit"
rng = np.random.default_rng(20261107)


def tvec(dur):
    return np.arange(int(round(dur * SR))) / SR


def smooth(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def spectral_mask(x, fn):
    n = x.shape[-1]
    X = np.fft.rfft(x, axis=-1)
    f = np.fft.rfftfreq(n, 1 / SR)
    return np.fft.irfft(X * fn(f), n, axis=-1)


def lowpass(x, fc, order=2):
    return spectral_mask(x, lambda f: 1 / (1 + (f / fc) ** (2 * order)))


def highpass(x, fc, order=2):
    return spectral_mask(x, lambda f: 1 - 1 / (1 + (f / fc) ** (2 * order)))


def pink(n):
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    f[0] = f[1]
    return np.fft.irfft(X / np.sqrt(f / 100), n)


def sweep_noise(n, fc_curve, bw_oct=0.9):
    """ピンクノイズを、中心周波数が時間で動くバンドパスに通す（STFT）。"""
    N, hop = 2048, 512
    pad = np.concatenate([np.zeros(N), pink(n), np.zeros(N)])
    out = np.zeros_like(pad)
    win = np.hanning(N)
    f = np.fft.rfftfreq(N, 1 / SR)
    f[0] = 1
    lf = np.log2(f)
    for s in range(0, len(pad) - N, hop):
        c = min(max(s + N // 2 - N, 0), n - 1)
        mask = np.exp(-0.5 * ((lf - np.log2(fc_curve[c])) / bw_oct) ** 2)
        out[s:s + N] += np.fft.irfft(np.fft.rfft(pad[s:s + N] * win) * mask, N) * win
    y = out[N:N + n] / 1.5
    return y / (np.std(y) + 1e-12)


def log_sweep(n, f0, f1, f2, peak):
    u = np.arange(n) / n
    a = smooth(u / peak)
    b = smooth((u - peak) / (1 - peak))
    lf = np.where(u < peak, np.log(f0) + (np.log(f1) - np.log(f0)) * a,
                  np.log(f1) + (np.log(f2) - np.log(f1)) * b)
    return np.exp(lf)


def bell_env(n, peak):
    u = np.arange(n) / n
    k = np.log(0.5) / np.log(peak)
    return np.sin(np.pi * u ** k) ** 2


def fade(x, fin=0.004, fout=0.03):
    n = x.shape[0]
    a, b = int(fin * SR), min(int(fout * SR), n // 3)
    g = np.ones(n)
    if a:
        g[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    if b:
        g[-b:] *= 0.5 + 0.5 * np.cos(np.pi * np.arange(b) / b)
    return x * (g[:, None] if x.ndim == 2 else g)


def stereo(m):
    return np.stack([m, m], 1)


# ---------------- 各音 ----------------

def whoosh_soft():
    n = int(0.7 * SR)
    fc = log_sweep(n, 400, 2200, 900, 0.42)
    A, B, C = (sweep_noise(n, fc) for _ in range(3))
    y = np.stack([A + 0.4 * B, A + 0.4 * C], 1) * bell_env(n, 0.42)[:, None]
    return fade(lowpass(y.T, 6000).T, 0.05, 0.12)


def whoosh_line():
    n = int(1.0 * SR)
    t = tvec(1.0)
    fc = log_sweep(n, 400, 2200, 900, 0.3)
    A, B = sweep_noise(n, fc), sweep_noise(n, fc)
    env = bell_env(n, 0.3)
    m = (A + 0.25 * B) * env
    # 薄い音程のきらめき: C#5 から D5 へ半音上昇、と1オクターブ上
    glide = 2 ** (-1 / 12 * (1 - smooth(t / 0.7)))
    sh = np.zeros(n)
    for mult, amp in ((1, 1.0), (2, 0.5)):
        sh += amp * np.sin(2 * np.pi * np.cumsum(D5 * mult * glide) / SR)
    sh *= env ** 1.5 * 0.10
    mono = lowpass(m, 6500) + sh
    ang = (smooth(t / 0.9) * 0.9 + 0.05) * np.pi / 2     # 等パワーで L -> R
    y = np.stack([mono * np.cos(ang), mono * np.sin(ang)], 1)
    return fade(y, 0.05, 0.15)


def riser_soft():
    dur, stop = 1.2, 1.0
    n = int(dur * SR)
    t = tvec(dur)
    rise = np.clip(t / stop, 0, 1)
    fc = 300 * (3200 / 300) ** (rise ** 1.3)
    A, B = sweep_noise(n, fc, 1.0), sweep_noise(n, fc, 1.0)
    amp = np.where(t < stop, rise ** 2.2, 0) + np.where(t >= stop, np.exp(-(t - stop) / 0.06), 0)
    tail = np.where(t >= stop, 0.22 * np.exp(-(t - stop) / 0.08), 0)
    noise = np.stack([A, B], 1) * (0.55 * amp + 0.25 * tail)[:, None]
    pad = np.zeros((n, 2))
    glide = 0.85 + 0.15 * smooth(rise)
    for f, pan in ((D4 / 2, 0.3), (D4, 0.5), (D4 * 1.5, 0.7), (FS5 / 2, 0.4)):
        s = np.sin(2 * np.pi * np.cumsum(f * glide) / SR) * (amp * 0.18 + tail * 0.5)
        pad[:, 0] += s * np.cos(pan * np.pi / 2)
        pad[:, 1] += s * np.sin(pan * np.pi / 2)
    y = lowpass((noise + pad).T, 6000).T
    return fade(y, 0.2, 0.08)


def pop(f0):
    n = int(0.25 * SR)
    t = tvec(0.25)
    f = f0 * (1 + 0.6 * np.exp(-t / 0.008))              # 1.6x -> 1x を約 25 ms で
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * (1 - np.exp(-t / 0.0015)) * np.exp(-t / 0.055)
    res = np.sin(2 * np.pi * f0 * 2.0 * t) * 0.10 * np.exp(-t / 0.05)    # 小さな胴鳴り
    res += np.sin(2 * np.pi * f0 * 0.5 * t) * 0.18 * np.exp(-t / 0.04)
    click = lowpass(rng.standard_normal(n), 3000) * np.exp(-t / 0.002) * 0.05
    y = lowpass(body + res + click, 8000)
    st = stereo(y)
    d = int(0.0003 * SR)                                  # 左右を 0.3 ms ずらして丸みを出す
    st[d:, 1] = y[:-d]
    return fade(st, 0.001, 0.06)


def tick(f0, seed):
    n = int(0.05 * SR)
    t = tvec(0.05)
    r = np.random.default_rng(seed)
    f = f0 * (1 + 0.25 * np.exp(-t / 0.004))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.010)
    y += 0.45 * np.sin(2 * np.pi * f0 * 2.4 * t) * np.exp(-t / 0.006)     # 木の2つ目のモード
    y += 0.20 * lowpass(r.standard_normal(n), 2500) * np.exp(-t / 0.0015)
    return fade(stereo(lowpass(y, 5000)), 0.0008, 0.015)


def land_soft():
    n = int(0.9 * SR)
    t = tvec(0.9)
    f = 60 + 25 * np.exp(-t / 0.02)
    sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * (1 - np.exp(-t / 0.003)) * np.exp(-t / 0.13)
    air = np.zeros((n, 2))
    for c in range(2):
        air[:, c] = highpass(lowpass(pink(n), 3500), 500) * np.exp(-t / 0.09) * (1 - np.exp(-t / 0.008))
    air = air / (np.std(air) + 1e-12) * 0.14
    bell = np.zeros(n)
    for mult, amp, tau in ((1, 1.0, 0.20), (2.0, 0.28, 0.12), (2.76, 0.10, 0.07), (3.0, 0.08, 0.1)):
        bell += amp * np.sin(2 * np.pi * D5 * mult * t) * np.exp(-t / tau)
    bell *= (1 - np.exp(-t / 0.004)) * 0.35
    y = stereo(sub * 0.9) + air + stereo(bell)
    return fade(lowpass(y.T, 7500).T, 0.001, 0.2)


def chime_logo():
    n = int(2.5 * SR)
    t = tvec(2.5)
    notes = [(D4, 0.00, 1.0), (D4 * 1.5, 0.05, 0.7), (D5, 0.10, 0.8), (FS5, 0.16, 0.55), (A5, 0.22, 0.4)]
    out = np.zeros((n, 2))
    for k, (f, delay, amp) in enumerate(notes):
        tt = np.clip(t - delay, 0, None)
        env = (1 - np.exp(-tt / 0.012)) * np.exp(-tt / (0.9 - 0.1 * k)) * (t >= delay)
        for side, det in ((0, 0.9987), (1, 1.0013)):      # 約 ±2 cent のコーラス
            ff = f * det
            ph = 2 * np.pi * ff * tt
            s = np.sin(ph + 1.1 * np.exp(-tt / 0.25) * np.sin(ph * 2.0))   # FM は最初だけ少し
            s += 0.18 * np.sin(2 * np.pi * ff * 3.0 * tt) * np.exp(-tt / 0.3)
            out[:, side] += s * env * amp * 0.5
            out[:, 1 - side] += s * env * amp * 0.12      # 逆側にも少し流して広がりを保つ
    d = int(0.011 * SR)                                   # 右だけごく少し遅らせて厚みを出す
    out[d:, 1] = out[d:, 1] * 0.5 + out[:-d, 1] * 0.5
    return fade(lowpass(out.T, 7500).T, 0.006, 0.4)


def ui_tap():
    n = int(0.08 * SR)
    t = tvec(0.08)
    f = 330 * (1 + 0.5 * np.exp(-t / 0.006))
    y = np.sin(2 * np.pi * np.cumsum(f) / SR) * (1 - np.exp(-t / 0.001)) * np.exp(-t / 0.018)
    y += 0.12 * lowpass(rng.standard_normal(n), 2200) * np.exp(-t / 0.002)
    return fade(stereo(lowpass(y, 5000)), 0.001, 0.02)


# ---------------- 仕上げ ----------------

# 有音区間 RMS の目標 (dBFS)。ピーク -6 dBFS に正規化したあと、これより大きければ下げる。
TARGET_RMS = {
    "whoosh_soft": -26, "whoosh_line": -26, "riser_soft": -27,
    "pop_a": -23, "pop_b": -23, "pop_c": -23,
    "tick_1": -37, "tick_2": -37, "tick_3": -37, "tick_4": -37, "tick": -37,
    "land_soft": -24, "chime_logo": -26, "ui_tap": -31,
}


def active_rms_db(y):
    """50 ms ごとの RMS のうち、最大から 20 dB 以内のブロックだけで RMS を取る。"""
    m = y.mean(1) if y.ndim == 2 else y
    b = int(0.05 * SR)
    nb = max(len(m) // b, 1)
    blocks = np.array([np.mean(m[i * b:(i + 1) * b] ** 2) for i in range(nb)]) if len(m) >= b \
        else np.array([np.mean(m ** 2)])
    keep = blocks > blocks.max() * 0.01
    return 10 * np.log10(np.mean(blocks[keep]) + 1e-20)


def finish(name, y):
    y = y - y.mean(0)
    y = y / np.abs(y).max() * 10 ** (-6 / 20)
    d = TARGET_RMS[name] - active_rms_db(y)
    return y * 10 ** (min(d, 0) / 20)


def write_wav(path, y):
    q = np.clip(np.round(y * 8388607), -8388608, 8388607).astype("<i4")
    b = np.frombuffer(q.tobytes(), dtype=np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(SR)
        w.writeframes(b)


def png_write(path, rgb):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[i].tobytes() for i in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def spectrogram_png(path, y, width=1600, height=480):
    """縦軸は 0〜24 kHz の線形。5 kHz ごとに目盛り線。"""
    m = y.mean(1)
    N, hop = 2048, max(len(m) // width, 256)
    win = np.hanning(N)
    S = 20 * np.log10(np.array([np.abs(np.fft.rfft(m[s:s + N] * win)) for s in range(0, len(m) - N, hop)]).T + 1e-9)
    S = S[::-1]
    rows = np.linspace(0, S.shape[0], height + 1).astype(int)
    S = np.array([S[rows[i]:max(rows[i + 1], rows[i] + 1)].max(0) for i in range(height)])
    v = np.clip((S - (S.max() - 90)) / 90, 0, 1)
    r = np.clip(v * 3 - 1.0, 0, 1)
    g = np.clip(v * 3 - 2.0, 0, 1)
    b = np.clip(v * 2.2, 0, 1) * (1 - g * 0.6)
    rgb = (np.stack([r, g, b], 2) * 255).astype(np.uint8)
    for hz in range(5000, 24000, 5000):
        rgb[int(height * (1 - hz / 24000)), ::4] = (90, 160, 90)
    png_write(path, np.ascontiguousarray(rgb))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sounds = {
        "whoosh_soft": whoosh_soft(), "whoosh_line": whoosh_line(), "riser_soft": riser_soft(),
        "pop_a": pop(D5), "pop_b": pop(FS5), "pop_c": pop(A5),
        "tick_1": tick(880.0, 1), "tick_2": tick(987.77, 2), "tick_3": tick(1108.73, 3), "tick_4": tick(1174.66, 4),
        "land_soft": land_soft(), "chime_logo": chime_logo(), "ui_tap": ui_tap(),
    }
    sounds["tick"] = sounds["tick_1"]
    done = {k: finish(k, v) for k, v in sounds.items()}
    for k, v in done.items():
        write_wav(OUT / f"{k}.wav", v)

    gap = np.zeros((int(0.6 * SR), 2))
    parts = []
    for k in ["whoosh_soft", "whoosh_line", "riser_soft", "pop_a", "pop_b", "pop_c", "land_soft", "chime_logo", "ui_tap"]:
        parts += [done[k], gap]
    run = np.zeros((int(15 * 0.07 * SR) + int(0.1 * SR), 2))      # tick 15 個、70 ms 間隔
    for i in range(15):
        s = int(i * 0.07 * SR)
        tk = done[f"tick_{[1, 3, 2, 4][i % 4]}"]
        run[s:s + len(tk)] += tk
    parts += [run, gap]
    prev = np.concatenate(parts)
    write_wav(OUT / "preview.wav", prev)
    spectrogram_png(OUT / "preview_spectrogram.png", prev)

    print(f"{'file':<13}{'sec':>6}{'peak':>8}{'RMS':>8}{'activeRMS':>10}{'>8k %':>8}{'DC':>9}")
    for k, v in list(done.items()) + [("preview", prev)]:
        X = np.abs(np.fft.rfft(v.mean(1))) ** 2
        hi = 100 * X[np.fft.rfftfreq(len(v), 1 / SR) > 8000].sum() / X.sum()
        print(f"{k:<13}{len(v)/SR:6.2f}{20*np.log10(np.abs(v).max()):8.1f}{10*np.log10(np.mean(v**2)):8.1f}"
              f"{active_rms_db(v):10.1f}{hi:8.3f}{abs(v.mean()):9.1e}")


if __name__ == "__main__":
    main()
