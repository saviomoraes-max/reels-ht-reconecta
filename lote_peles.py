#!/usr/bin/env python3
"""Roda o montar_ht_peles.py UMA SAÍDA POR VEZ, com vigia de travamento.

Por quê (06/10/2026): o disco Cyberpunk caiu e voltou às 11h44 e o ffmpeg ficou parado esperando o
disco, sem CPU e sem crescer o arquivo, por quase 3 h. O lote não percebia. Aqui cada saída é um
processo; se o .part não cresce por `--parado` s, o processo inteiro (python + ffmpeg) é morto e a
saída é refeita (até `--tentativas`).

uso:
  python3 lote_peles.py planos/ht_sem41_terca.json --versao HT ORG              # tudo, na ordem
  python3 lote_peles.py planos/ht_sem41_terca.json --versao HT --so VD012_B VD013_A
  python3 lote_peles.py planos/ht_sem41_terca.json --versao HT --so VD004_A --forcar
Log (linha a linha, com hora e pico de RAM do ffmpeg): <destino>/_lote/lote.log
"""
import argparse, json, os, signal, subprocess, sys, time, unicodedata

AQUI = os.path.dirname(os.path.abspath(__file__))

def nfc(s):
    return unicodedata.normalize('NFC', s)

def rss_ffmpeg_mb():
    out = subprocess.run(['ps', '-axo', 'rss,comm'], capture_output=True, text=True).stdout
    return sum(int(l.split()[0]) for l in out.splitlines()[1:] if l.strip().endswith('ffmpeg')) / 1024

def achar(pasta, nome):
    if not os.path.isdir(pasta):
        return None
    for f in os.listdir(pasta):
        if nfc(f) == nfc(nome):
            return os.path.join(pasta, f)
    return None

LIMITE_ORG = 199e6          # Publer: 200e6 bytes por arquivo; 195e6 refazia à toa um vídeo de 196 MB (VD012_C, 13/10)


def limpar_temporarios(destino):
    """Lote 100 % ok: apaga o que o render refaz sozinho se precisar (pedido do Sávio, 13/10: "vai apagando os
    arquivos obsoletos pra gente sempre ter espaço"). Vídeo final, conferência, relatório e log ficam."""
    import shutil
    for sub in ('_peles_tmp', '_amostra'):
        alvo = os.path.join(destino, sub)
        if os.path.isdir(alvo):
            shutil.rmtree(alvo, ignore_errors=True)
    for raiz, _, arqs in os.walk(destino):
        for nome in arqs:
            if nome.endswith(('.part', '.part.mp4')):
                os.remove(os.path.join(raiz, nome))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('plano')
    ap.add_argument('--versao', nargs='+', default=['HT', 'ORG'])
    ap.add_argument('--so', nargs='*', help='só estes rótulos (ex.: VD012_B VD013_A)')
    ap.add_argument('--forcar', action='store_true')
    ap.add_argument('--parado', type=int, default=120, help='s sem o .part crescer pra considerar travado')
    ap.add_argument('--tentativas', type=int, default=3)
    a = ap.parse_args()
    plano = json.load(open(a.plano, encoding='utf-8'))
    destino = plano['destino']
    os.makedirs(os.path.join(destino, '_lote'), exist_ok=True)
    log = open(os.path.join(destino, '_lote', 'lote.log'), 'a', encoding='utf-8', buffering=1)

    def diz(msg):
        linha = f"{time.strftime('%H:%M:%S')} {msg}"
        print(linha, flush=True)
        log.write(linha + '\n')

    variantes = {v['id']: v for v in plano['variantes']}
    fila = []
    for vid in a.versao:
        for v in plano['videos']:
            for letra in sorted(v['hooks']):
                rot = f"{v['vd']}_{letra}"
                if a.so and rot not in a.so:
                    continue
                fila.append((vid, rot))
    diz(f"== lote {', '.join(a.versao)}: {len(fila)} saídas")
    falhas = []
    for n, (vid, rot) in enumerate(fila, 1):
        var = variantes[vid]
        pasta = os.path.join(destino, var['subpasta'])
        nome = f"{var['prefixo']}{rot}.mp4"
        cmd = [sys.executable, '-u', os.path.join(AQUI, 'montar_ht_peles.py'), a.plano, '--so', rot, '--versao', vid]
        if a.forcar:
            cmd.append('--forcar')
        for tent in range(1, a.tentativas + 1):
            ini = time.time()
            p = subprocess.Popen(cmd, cwd=AQUI, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 start_new_session=True)
            os.set_blocking(p.stdout.fileno(), False)
            ultimo_tam, ultimo_cresc, pico, saida = -1, time.time(), 0.0, []
            travou = False
            while p.poll() is None:
                time.sleep(15)
                try:
                    pedaco = p.stdout.read()
                    if pedaco:
                        saida.append(pedaco)
                except (BlockingIOError, TypeError):
                    pass
                pico = max(pico, rss_ffmpeg_mb())
                # o .part pode estar na pasta da versão ou na pasta própria do vídeo (orgânico organizado)
                part = achar(pasta, nome + '.part.mp4') or achar(os.path.join(pasta, nome[:-4]), nome + '.part.mp4')
                tam = os.path.getsize(part) if part and os.path.exists(part) else -1
                if tam != ultimo_tam:
                    ultimo_tam, ultimo_cresc = tam, time.time()
                elif tam >= 0 and time.time() - ultimo_cresc > a.parado:
                    diz(f"   [{vid} {rot}] TRAVOU: .part parado em {tam/1e6:.0f} MB há {a.parado}s — matando e refazendo")
                    os.killpg(p.pid, signal.SIGKILL)
                    travou = True
                    break
            try:
                resto = p.stdout.read()
                if resto:
                    saida.append(resto)
            except Exception:
                pass
            p.wait()
            texto = ''.join(saida).strip()
            ultima = [l for l in texto.splitlines() if l.strip().startswith('[')]
            if not travou and p.returncode == 0:
                # orgânico acima de LIMITE_ORG não sobe no Publer (limite 200.000.000 bytes): refaz com x264 veryslow, MESMO CRF 18
                # (mesma qualidade, arquivo menor) — 06/10/2026, VD021_C saiu com 213 MB
                fin = achar(pasta, nome) or achar(os.path.join(pasta, nome[:-4]), nome)
                if vid == 'ORG' and fin and os.path.getsize(fin) > LIMITE_ORG and '--preset' in cmd and '--codec' not in cmd:
                    # veryslow não bastou (VD034_C da SEM41 sexta: 222 → 204 MB): HEVC na mesma qualidade
                    diz(f"   [{vid} {rot}] ainda {os.path.getsize(fin)/1e6:.0f} MB com veryslow: refazendo em HEVC (x265 CRF 20)")
                    cmd = [c for c in cmd if c not in ('--preset', 'veryslow')] + ['--codec', 'hevc']
                    continue
                if vid == 'ORG' and fin and os.path.getsize(fin) > LIMITE_ORG and '--preset' not in cmd:
                    diz(f"   [{vid} {rot}] {os.path.getsize(fin)/1e6:.0f} MB passa do limite do Publer: refazendo com preset veryslow (mesmo CRF)")
                    cmd = cmd + ['--forcar', '--preset', 'veryslow']
                    continue
                diz(f"[{n}/{len(fila)}] {vid} {rot} ok em {time.time()-ini:.0f}s, pico RAM {pico:.0f} MB | "
                    + (ultima[-1].strip() if ultima else texto[-200:]))
                break
            if not travou:
                diz(f"   [{vid} {rot}] ERRO (código {p.returncode}), tentativa {tent}: {texto[-600:]}")
        else:
            falhas.append(f"{vid} {rot}")
            diz(f"[{n}/{len(fila)}] {vid} {rot} FALHOU nas {a.tentativas} tentativas")
    diz(f"== fim: {len(fila) - len(falhas)}/{len(fila)} ok" + (f"; falharam: {falhas}" if falhas else ""))
    open(os.path.join(destino, '_lote', 'lote.fim'), 'w').write('\n'.join(falhas))
    if not falhas:                                 # com falha, o temporário fica pra diagnóstico
        limpar_temporarios(destino)
        diz("== temporários apagados (_peles_tmp, _amostra, .part)")

if __name__ == '__main__':
    main()
