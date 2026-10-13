#!/usr/bin/env python3
"""Monta anúncios HT com PELES de edição diferentes, pra as variações não parecerem o mesmo vídeo
(pedido do Sávio, 06/10/2026: "todos os vídeos ficaram muito parecidos").

Cada gancho do plano escolhe:
  pele      manchete  — vídeo em cartão arredondado sobre o próprio vídeo desfocado + título fixo no topo
            cinema    — LUT de cinema (Cineva CINELUTS), grão 35 mm, vinheta; transição = queima de filme + whoosh
            dinamica  — zoom de corte (1,15×) a cada troca de frase; transição = glitch + SFX
  abertura  flash     — gancho → transição da pele → Reel do início (manchete usa o flash branco + obturador)
            rebobinar — gancho → 1 s do Reel voltando de trás pra frente até o início (VHS + "fita rebobinando") → Reel
            pergunta  — os primeiros 1,6 s do gancho desfocados com o título escrito grande; depois o gancho segue nítido
  + `momentos` do vídeo (números ditos na fala): destaque animado (texto Grift Black creme + marcador à mão do Cineva + impacto)

uso:
  python3 montar_ht_peles.py plano.json                          # tudo (uma saída por vez)
  python3 montar_ht_peles.py plano.json --so VD004_A --versao HT
  python3 montar_ht_peles.py plano.json --so VD004_A --versao HT --ate 25 --amostra     # prévia curta em _amostra/
  python3 montar_ht_peles.py plano.json --so VD004_A --versao HT --trecho 40 52 --amostra

MEMÓRIA (regra permanente do Sávio): gancho, corpo e rebobinada entram como ENTRADAS separadas do mesmo
arquivo (-ss/-t); nada de split→concat do corpo. Efeitos entram como overlay com alfa (luma vira alfa)
deslocado no tempo, sem segurar quadro. Uma saída por vez.
Herda do montar_ht_hooks.py o corte do gancho (folgas, congelamento, ajustes finos do blocos_legenda.py).
"""
import argparse, json, math, os, shutil, subprocess, sys
import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))   # raiz do projeto, onde mora o config.py
import config  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from montar_ht_hooks import duracao, fps_de, palavras, corte_do_gancho, luma_frame, variantes_do  # noqa: E402
from frases import frases  # noqa: E402

KIT_JSON = os.path.join(config.KIT_DIR, 'kit.json')
FONTES = config.FONTES_DIR
FONTE_FORTE = config.FONTE_FORTE
FONTE_VHS = config.FONTE_VHS
CREME = (250, 240, 221)            # cor.texto/display do design system
CHAMPAGNE = (242, 221, 182)        # cor.texto/enfase

REBOBINAR = 1.0                    # s de fita voltando
PERGUNTA = 1.6                     # s de título sobre o gancho desfocado
ZOOM, ZOOM_CY = 1.15, 860          # punch-in e a altura do centro (rosto/peito; legenda queimada continua no quadro)
ZOOM_MIN = 3.0                     # s mínimos em cada enquadramento
# peles LIMPAS (Sávio, 12/10/2026: "mais clean, parecer um vídeo que não foi tão editado, mas versões diferentes"):
#   limpa = gancho e Reel como gravados; zoom = só o gancho num enquadramento mais fechado (outra "câmera");
#   texto = caixa de texto no estilo nativo do Instagram sobre o gancho. Abertura "corte" = corte seco, sem flash nem som.
PELES_LIMPAS = ('limpa', 'zoom', 'texto')
ZOOM_LIMPO = 1.10
FONTE_NATIVA = config.FONTE_NATIVA
TEXTO_NATIVO_CY = 1130
# tela dividida (13/10): B-roll em cima, a Ana embaixo, legenda nossa logo acima da emenda, headline 4 s no topo
EMENDA = 956                       # altura da metade de cima (o acervo normalizado é 1080×956)
ANA_H = 1920 - EMENDA              # 964: janela da Ana recortada do Reel (y do topo vem do plano: `dividida_y`)
HEADLINE_DUR = 4.0
HEADLINE_CY = 250                  # centro da headline no alto da metade de cima
FONTE_LEGENDA = config.FONTE_LEGENDA
LEGENDA_H = 220                    # altura da faixa da legenda (a base do texto fica ~22 px acima da emenda)
BROLL_MIN, BROLL_MAX = 2.4, 4.2    # s de cada clipe em cima (média ~3,3 s; referência medida 2,2–3,4 s)
CARTAO = (108, 384, 864, 1536)     # manchete: x, y, largura, altura do vídeo (0,8×)
LUT_FORCA = 0.6                    # cinema: 60 % LUT + 40 % original (pele não fica laranja)
GRAO_FORCA = 0.30
DESTAQUE_DUR = 2.4                 # s do destaque de número na tela
DESTAQUE_CY = 1110                 # centro vertical do destaque: peito, entre o queixo e a legenda queimada
# trecho útil de cada efeito do kit (medido 06/10): início, duração e o pico de brilho dentro do trecho
EFEITO = {
    'queima': {'ini': 0.40, 'dur': 0.70, 'pico': 0.40},
    'glitch': {'ini': 0.10, 'dur': 0.70, 'pico': 0.40},
}

def ff(args):
    subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', *args], check=True)

def fonte(caminho, tam):
    return ImageFont.truetype(caminho, tam)

def quebrar(texto, f, largura, draw):
    linhas, atual = [], ''
    for p in texto.split():
        teste = (atual + ' ' + p).strip()
        if draw.textlength(teste, font=f) <= largura or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = p
    if atual:
        linhas.append(atual)
    return linhas

def texto_com_sombra(img, linhas, f, cx, y0, entre, cor, sombra=0.55, raio=10):
    """Escreve linhas centradas em cx a partir de y0, com sombra suave (não text-shadow duro)."""
    camada = Image.new('RGBA', img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(camada)
    alt = f.size * entre
    for i, l in enumerate(linhas):
        w = d.textlength(l, font=f)
        d.text((cx - w / 2, y0 + i * alt + 5), l, font=f, fill=(0, 0, 0, int(255 * sombra)))
    camada = camada.filter(ImageFilter.GaussianBlur(raio))
    img.alpha_composite(camada)
    d = ImageDraw.Draw(img)
    for i, l in enumerate(linhas):
        w = d.textlength(l, font=f)
        d.text((cx - w / 2, y0 + i * alt), l, font=f, fill=cor + (255,))
    return y0 + len(linhas) * alt

def ajustar(texto, caminho, tam, minimo, largura, max_linhas):
    d = ImageDraw.Draw(Image.new('RGBA', (8, 8)))
    while True:
        f = fonte(caminho, tam)
        linhas = quebrar(texto, f, largura, d)
        if len(linhas) <= max_linhas or tam <= minimo:
            return f, linhas
        tam -= 4

# ---------- PNGs das peles ----------
def png_manchete(titulo, saida):
    img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    f, linhas = ajustar(titulo, FONTE_FORTE, 74, 52, 920, 3)
    alt = f.size * 1.12 * len(linhas)
    y0 = 168 + (196 - alt) / 2                    # faixa útil 168–364 (acima: abas do TikTok; abaixo: cartão em 384)
    texto_com_sombra(img, linhas, f, 540, y0, 1.12, CREME)
    img.save(saida)

def png_mascara_cartao(saida):
    _, _, w, h = CARTAO
    m = Image.new('L', (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=42, fill=255)
    m.save(saida)

def png_pergunta(titulo, saida):
    img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    f, linhas = ajustar(titulo, FONTE_FORTE, 96, 64, 900, 4)
    alt = f.size * 1.1 * len(linhas)
    y0 = 760 - alt / 2
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((470, y0 - 46, 610, y0 - 38), radius=4, fill=CHAMPAGNE + (255,))   # fio champagne acima
    texto_com_sombra(img, linhas, f, 540, y0, 1.1, CREME, sombra=0.7, raio=14)
    img.save(saida)

def png_texto_nativo(titulo, saida):
    """Caixa branca arredondada com texto preto centralizado, no jeito do texto "Clássico com fundo" do Instagram."""
    img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f, linhas = ajustar(titulo, FONTE_NATIVA, 58, 44, 820, 3)
    alt = int(f.size * 1.28)
    larg = max(int(d.textlength(l, font=f)) for l in linhas)
    # centro em y≈1130 (entre o queixo e a legenda queimada): no topo muitos Reels da Ana já têm título/print próprio
    y0 = int(TEXTO_NATIVO_CY - alt * len(linhas) / 2)
    for i, l in enumerate(linhas):               # uma pílula por linha, como o Instagram faz
        w = int(d.textlength(l, font=f))
        x0 = 540 - w // 2
        d.rounded_rectangle((x0 - 26, y0 + i * alt - 10, x0 + w + 26, y0 + i * alt + alt - 4), radius=18, fill=(255, 255, 255, 255))
    for i, l in enumerate(linhas):
        w = int(d.textlength(l, font=f))
        d.text((540 - w / 2, y0 + i * alt + 2), l, font=f, fill=(0, 0, 0, 255))
    img.save(saida)

def png_headline(titulo, saida):
    """Headline da tela dividida: mesma caixa branca do texto nativo do Instagram, maior, no alto da metade de cima."""
    img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f, linhas = ajustar(titulo, FONTE_NATIVA, 66, 48, 860, 3)
    alt = int(f.size * 1.28)
    y0 = int(HEADLINE_CY - alt * len(linhas) / 2)
    for i, l in enumerate(linhas):
        w = int(d.textlength(l, font=f))
        x0 = 540 - w // 2
        d.rounded_rectangle((x0 - 28, y0 + i * alt - 10, x0 + w + 28, y0 + i * alt + alt - 4), radius=20, fill=(255, 255, 255, 255))
    for i, l in enumerate(linhas):
        w = int(d.textlength(l, font=f))
        d.text((540 - w / 2, y0 + i * alt + 2), l, font=f, fill=(0, 0, 0, 255))
    img.save(saida)

def grupos_legenda(pals_saida):
    """Palavras (início, fim, texto) no tempo da saída → grupos de 1 a 3 palavras (≤ 20 caracteres),
    quebrando em pontuação e em pausa > 0,6 s."""
    grupos, atual = [], []
    for n, (s, e, t) in enumerate(pals_saida):
        if atual and (len(atual) == 3 or len(' '.join(x[2] for x in atual + [(s, e, t)])) > 20
                      or s - atual[-1][1] > 0.6):
            grupos.append(atual)
            atual = []
        atual.append((s, e, t))
        if t.rstrip()[-1:] in '.,?!:;':
            grupos.append(atual)
            atual = []
    if atual:
        grupos.append(atual)
    return grupos

def camada_legenda(pals_saida, total, fps, pasta):
    """Legenda branca sem caixa, palavra entrando uma a uma, como sequência de PNGs com duração (concat demuxer).
    Devolve o caminho da lista do concat (faixa 1080×LEGENDA_H com alfa)."""
    os.makedirs(pasta, exist_ok=True)
    f, _ = ajustar('x', FONTE_LEGENDA, 68, 68, 1000, 1)
    vazio = os.path.join(pasta, 'vazio.png')
    Image.new('RGBA', (1080, LEGENDA_H), (0, 0, 0, 0)).save(vazio)
    estados = []                                    # (início, fim, texto)
    grupos = grupos_legenda(pals_saida)
    for g_i, g in enumerate(grupos):
        fim_g = g[-1][1] + 0.5
        if g_i + 1 < len(grupos):
            fim_g = min(fim_g, grupos[g_i + 1][0][0])
        for k in range(len(g)):
            t0 = g[k][0]
            t1 = g[k + 1][0] if k + 1 < len(g) else fim_g
            if t1 - t0 > 0.02:
                estados.append((t0, t1, ' '.join(x[2] for x in g[:k + 1])))
    linhas, t, quadro = [], 0.0, 1.0 / fps
    def item(arq, d):
        linhas.append(f"file '{arq}'")
        linhas.append(f"duration {max(quadro, d):.4f}")
    for n, (t0, t1, texto) in enumerate(estados):
        t0, t1 = max(t0, t), min(t1, total)
        if t1 <= t0:
            continue
        if t0 - t > 0.001:
            item(vazio, t0 - t)
        img = Image.new('RGBA', (1080, LEGENDA_H), (0, 0, 0, 0))
        f_t, ls = ajustar(texto, FONTE_LEGENDA, 68, 52, 1000, 1)
        texto_com_sombra(img, ls, f_t, 540, LEGENDA_H - 22 - f_t.size * 1.05, 1.1, (255, 255, 255), sombra=0.7, raio=8)
        arq = os.path.join(pasta, f'l{n:04d}.png')
        img.save(arq)
        item(arq, t1 - t0)
        t = t1
    if total - t > 0.001:
        item(vazio, total - t)
    linhas.append(f"file '{vazio}'")              # o concat ignora a duração do último item
    lista = os.path.join(pasta, 'lista.txt')
    with open(lista, 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(linhas) + '\n')
    return lista

def faixa_broll(cfg, v, hk, rotulo, total, fps, pasta):
    """Monta a metade de cima: clipes do `broll` do gancho, cada um 2,4–4,2 s (sorteio fixo pelo rótulo),
    um arquivo por clipe (memória baixa) e concat sem re-encode. Devolve o caminho do .mp4 1080×EMENDA."""
    import random
    os.makedirs(pasta, exist_ok=True)
    rnd = random.Random(rotulo)
    lista = list(hk['broll'])
    if not lista:
        raise SystemExit(f'{rotulo}: gancho sem lista de broll (montar o plano com montar_plano_dia.py --estilo dividida)')
    base = cfg['broll_pasta']
    pedacos, t, n = [], 0.0, 0
    while t < total + 0.5:
        if n and n % len(lista) == 0:              # acabou a lista: reembaralha (só em vídeo muito longo)
            rnd.shuffle(lista)
        arq = os.path.join(base, lista[n % len(lista)])
        d_clip = duracao(arq)
        slot = min(rnd.uniform(BROLL_MIN, BROLL_MAX), max(1.0, d_clip - 0.1))
        ini = rnd.uniform(0, max(0.0, d_clip - slot - 0.05))
        p = os.path.join(pasta, f'b{n:03d}.mp4')
        ff(['-ss', f'{ini:.3f}', '-t', f'{slot:.3f}', '-i', arq, '-an',
            '-vf', f'fps={fps},scale=1080:{EMENDA},setsar=1,format=yuv420p,setpts=PTS-STARTPTS',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '12', p])
        pedacos.append(p)
        t += duracao(p)
        n += 1
    lst = os.path.join(pasta, 'lista.txt')
    with open(lst, 'w', encoding='utf-8') as fp:
        fp.write(''.join(f"file '{p}'\n" for p in pedacos))
    saida = os.path.join(pasta, 'broll.mp4')
    ff(['-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', saida])
    for p in pedacos:                              # os pedaços já estão dentro do broll.mp4
        os.remove(p)
    return saida

def filtro_caminho(caminho):
    """Caminho dentro de um filtergraph (`movie=`): entre aspas simples; dois-pontos e aspas não são aceitos."""
    if ':' in caminho or "'" in caminho:
        raise SystemExit(f'caminho com dois-pontos ou aspas não entra no filtro movie: {caminho}')
    return "'" + caminho + "'"

def pals_na_saida(pals, L):
    """Palavras do Reel no tempo da saída (antes da aceleração): as do gancho no começo, depois o corpo."""
    out = [(w['start'] - L['ini'], w['end'] - L['ini'], w['text']) for w in pals
           if w['start'] >= L['ini'] - 0.01 and w['end'] <= L['fim'] + 0.05]
    for w in pals:
        a, b = L['mapa'](w['start']), L['mapa'](w['end'])
        if a is not None and b is not None:
            out.append((L['tb'] + a, L['tb'] + b, w['text']))
    return [(s, e, t.strip()) for s, e, t in out if t.strip()]

def png_rebobinar(saida):
    img = Image.new('RGBA', (1080, 1920), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x, y, a = 84, 214, 46                         # dois triângulos ◀◀ + REBOBINANDO, estilo tela de VHS
    for k in (0, 1):
        x0 = x + k * 40
        d.polygon([(x0 + 40, y), (x0, y + a / 2), (x0 + 40, y + a)], fill=(255, 255, 255, 235))
    if os.path.exists(FONTE_VHS):
        d.text((x + 104, y + 2), 'REBOBINANDO', font=fonte(FONTE_VHS, 44), fill=(255, 255, 255, 235))
    img.save(saida)

def marcador_quadros(pasta):
    qs = sorted(f for f in os.listdir(pasta) if f.endswith('.png') and not f.startswith('._'))
    return [Image.open(os.path.join(pasta, q)).convert('RGBA') for q in qs]

def gerar_destaque(texto, tipo_marcador, pasta, kit, fps, linha2=None, tam_max=168):
    """Sequência PNG 1080×560 com alfa: número grande entra com 'pop' (+ complemento menor em champagne embaixo),
    marcador do Cineva se desenha em volta/embaixo, some no fim."""
    os.makedirs(pasta, exist_ok=True)
    W, H = 1080, 560
    d0 = ImageDraw.Draw(Image.new('RGBA', (8, 8)))
    tam = tam_max
    while d0.textlength(texto, font=fonte(FONTE_FORTE, tam)) > 860 and tam > 84:
        tam -= 6
    f = fonte(FONTE_FORTE, tam)
    tw = int(d0.textlength(texto, font=f))
    asc, desc = f.getmetrics()
    f2 = fonte(FONTE_FORTE, max(52, int(tam * 0.42))) if linha2 else None
    alt2 = int(f2.size * 1.25) if linha2 else 0
    # camada do texto já com sombra (linha 2 menor, champagne)
    camada = Image.new('RGBA', (max(tw, int(d0.textlength(linha2 or '', font=f2)) if linha2 else tw) + 80,
                                asc + desc + alt2 + 80), (0, 0, 0, 0))
    texto_com_sombra(camada, [texto], f, camada.width / 2, 40, 1.0, CREME, sombra=0.65, raio=12)
    if linha2:
        texto_com_sombra(camada, [linha2], f2, camada.width / 2, 40 + asc + desc - 6, 1.0, CHAMPAGNE, sombra=0.65, raio=10)
    quadros = marcador_quadros(kit['marcadores'][tipo_marcador])
    bbox = quadros[-1].getchannel('A').getbbox() or (0, 0, 1280, 720)
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    th = asc + desc
    if tipo_marcador == 'circulo':
        esc_x, esc_y = (tw * 1.3) / bw, (th * 1.38) / bh
    elif tipo_marcador == 'sublinhado':
        esc_x = esc_y = (tw * 1.08) / bw
    else:                                          # brilho: no canto de cima à direita
        esc_x = esc_y = (th * 1.1) / bh
    tingidos = []
    for q in quadros:
        c = q.crop(bbox).resize((max(1, int(bw * esc_x)), max(1, int(bh * esc_y))), Image.LANCZOS)
        r, g, b, a = c.split()                    # traço branco → champagne da marca
        c = Image.merge('RGBA', (r.point(lambda v: v * CHAMPAGNE[0] // 255), g.point(lambda v: v * CHAMPAGNE[1] // 255),
                                 b.point(lambda v: v * CHAMPAGNE[2] // 255), a))
        tingidos.append(c)
    n = int(DESTAQUE_DUR * fps)
    cx, cy = W // 2, H // 2
    for i in range(n):
        t = i / fps
        if t < 0.12:
            s = 0.55 + (1.08 - 0.55) * (t / 0.12)
        elif t < 0.22:
            s = 1.08 - 0.08 * ((t - 0.12) / 0.10)
        else:
            s = 1.0
        alfa = 1.0 if t < DESTAQUE_DUR - 0.3 else max(0.0, (DESTAQUE_DUR - t) / 0.3)
        quadro = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        tl = camada.resize((max(1, int(camada.width * s)), max(1, int(camada.height * s))), Image.LANCZOS)
        quadro.alpha_composite(tl, (int(cx - tl.width / 2), int(cy - tl.height / 2)))
        if t >= 0.08:
            m = tingidos[min(len(tingidos) - 1, int((t - 0.08) * fps))]
            topo = cy - camada.height * s / 2 + 40 * s          # topo da linha principal
            if tipo_marcador == 'circulo':
                pos = (int(cx - m.width / 2), int(topo + th * s / 2 - m.height / 2))
            elif tipo_marcador == 'sublinhado':
                pos = (int(cx - m.width / 2), int(topo + (th + alt2) * s - m.height * 0.25))
            else:
                pos = (int(cx + tw / 2 - m.width * 0.1), int(topo - m.height * 0.75))
            quadro.alpha_composite(m, (max(0, min(W - m.width, pos[0])), max(0, min(H - m.height, pos[1]))))
        if alfa < 1.0:
            quadro.putalpha(quadro.getchannel('A').point(lambda v: int(v * alfa)))
        quadro.save(os.path.join(pasta, f'q{i:03d}.png'))
    return n

# ---------- linha do tempo ----------
def mapa_cortes(cortes):
    """Função tempo do Reel → tempo do corpo depois de tirar os trechos da versão curta (None se cai num corte)."""
    cortes = sorted(cortes)
    def f(t):
        tirado = 0.0
        for a, b in cortes:
            if t >= b:
                tirado += b - a
            elif t > a:
                return None
        return t - tirado
    return f

def escolher_cortes(v, var, h, r, dur_reel, acel_max):
    """Versão sem tela final com limite (Shorts/Trial Reels ≤ 3 min): se passar do limite mesmo acelerando
    até acel_max, tira trechos da versão curta (na ordem do plano) até caber."""
    lim = var.get('dur_max')
    if var.get('tela_final', True) or not lim:
        return []
    escolhidos, corpo = [], dur_reel
    for c in v.get('cortes_curta') or []:
        if (h + r + corpo) / acel_max <= lim:
            break
        escolhidos.append((c['inicio_corte'], c['fim_corte']))
        corpo -= c['fim_corte'] - c['inicio_corte']
    return escolhidos

def transicao_de(hk):
    if hk['abertura'] == 'rebobinar':
        return 'rebobinar'
    if hk['abertura'] == 'corte':
        return 'corte'
    if hk['pele'] in PELES_LIMPAS or hk['pele'] == 'dividida':
        return 'flash'
    return {'manchete': 'flash', 'cinema': 'queima', 'dinamica': 'glitch'}[hk['pele']]

def segmentos_zoom(pals, dur_reel):
    """Liga/desliga o zoom no começo de frase, com pelo menos ZOOM_MIN s em cada enquadramento (tempo do Reel)."""
    segs, ligado, ultimo, ini = [], False, 0.0, 0.0
    for s, _e, _t in frases(pals):
        if s < 0.5 or s - ultimo < ZOOM_MIN:
            continue
        ligado = not ligado
        if ligado:
            ini = s - 0.04
        else:
            segs.append((ini, s - 0.04))
        ultimo = s
    if ligado:
        segs.append((ini, dur_reel))
    return segs

def planejar(cfg, v, hk, var, pals, dur_reel, fps):
    ini, fim, extra, fim_v = corte_do_gancho(pals, hk, cfg)
    h = round(fim - ini + extra, 3)
    r = REBOBINAR if hk['abertura'] == 'rebobinar' else 0.0
    cortes = escolher_cortes(v, var, h, r, dur_reel, cfg.get('acel_max', 1.08))
    mapa = mapa_cortes(cortes)
    corpo = dur_reel - sum(b - a for a, b in cortes)
    tb = h + r                                     # instante em que o Reel começa na saída
    bruto = tb + corpo
    com_tela = var.get('tela_final', True)
    fator = 1.0
    if com_tela:
        total = bruto + duracao(cfg['tela_final']) + cfg['pausa'] - cfg['fusao']
    else:
        lim = var.get('dur_max')
        if lim and bruto > lim:
            fator = math.ceil(bruto / lim * 1000) / 1000
        total = bruto / fator
    return {'ini': ini, 'fim': fim, 'extra': extra, 'fim_v': fim_v, 'h': h, 'r': r, 'tb': tb, 'corpo': corpo,
            'bruto': bruto, 'total': total, 'fator': fator, 'cortes': cortes, 'mapa': mapa, 'com_tela': com_tela}

# ---------- render ----------
def render(cfg, v, hk, var, pals, saida, tmp, kit, ate=None, trecho=None, preset='medium', codec='h264'):
    video = v['arquivo']
    fps = fps_de(video)
    dur_reel = duracao(video)
    L = planejar(cfg, v, hk, var, pals, dur_reel, fps)
    h, r, tb, mapa = L['h'], L['r'], L['tb'], L['mapa']
    pele, abertura, trans = hk['pele'], hk['abertura'], transicao_de(hk)
    os.makedirs(tmp, exist_ok=True)
    entradas, sfx = [], []                         # sfx: (nome, instante do pico na saída)

    def entrada(*args):
        entradas.extend(args)
        return sum(1 for x in entradas if x == '-i') - 1

    fmt_a = "aformat=sample_rates=48000:channel_layouts=stereo"
    flash = cfg['flash']
    f = []
    # --- gancho
    i_h = entrada('-ss', f"{L['ini']:.3f}", '-t', f"{L['fim'] - L['ini']:.3f}", '-i', video)
    congela_v = round((L['fim'] - L['fim_v']) + L['extra'], 3)
    trim_v = f"trim=end={L['fim_v'] - L['ini']:.3f}," if L['fim_v'] < L['fim'] else ""
    pad_v = f",tpad=stop_duration={congela_v}:stop_mode=clone" if congela_v > 0 else ""
    fade_h = f",fade=t=out:st={h - flash:.3f}:d={flash}:color=white" if trans == 'flash' else ""
    f.append(f"[{i_h}:v]{trim_v}setpts=PTS-STARTPTS,fps={fps},scale=1080:1920,setsar=1,format=yuv420p{pad_v}{fade_h}[hv0]")
    pad_a = f",apad=pad_dur={L['extra']}" if L['extra'] > 0 else ""
    f.append(f"[{i_h}:a]asetpts=PTS-STARTPTS,{fmt_a}{pad_a},afade=t=in:d=0.02,afade=t=out:st={h - 0.03:.3f}:d=0.03[ha]")
    if abertura == 'pergunta':
        p = os.path.join(tmp, 'pergunta.png')
        png_pergunta(hk['titulo'], p)
        i_p = entrada('-loop', '1', '-framerate', str(fps), '-t', f"{PERGUNTA:.3f}", '-i', p)
        f.append(f"[hv0]split[hA][hB]")
        f.append(f"[hB]trim=duration={PERGUNTA:.3f},setpts=PTS-STARTPTS,scale=270:480,boxblur=10:2,scale=1080:1920,"
                 f"eq=brightness=-0.20:saturation=0.8,format=rgba[hBb]")
        f.append(f"[hBb][{i_p}:v]overlay=0:0:format=auto:shortest=1,format=yuva420p,"
                 f"fade=t=out:st={PERGUNTA - 0.35:.3f}:d=0.35:alpha=1[hBt]")
        f.append(f"[hA][hBt]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[hv]")
        sfx.append(('pop', 0.08))
    else:
        f.append("[hv0]null[hv]")
    # --- rebobinada: o Reel do ponto do gancho até o 0, de trás pra frente, em REBOBINAR s
    partes = ["[hv][ha]"]
    if r > 0:
        i_r = entrada('-t', f"{L['fim']:.3f}", '-i', video)
        k = max(1, int(L['fim'] * fps / (r * fps)))
        f.append(f"[{i_r}:v]select='not(mod(n\\,{k}))',scale=1080:1920,setsar=1,format=yuv420p,reverse,"
                 f"setpts=N/({fps}*TB),fps={fps},trim=duration={r:.3f}[rv]")
        f.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={r:.3f}[ra]")
        partes.append("[rv][ra]")
        sfx.append(('rebobinar', h + 0.31))
    # --- corpo inteiro (com os cortes da versão curta, se houver)
    i_b = entrada('-i', video)
    sel_v = sel_a = ""
    if L['cortes']:
        expr = '+'.join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in L['cortes'])
        sel_v = f"select='not({expr})',setpts=N/({fps}*TB),"
        sel_a = f"aselect='not({expr})',asetpts=N/SR/TB,"
    fade_b = f",fade=t=in:st=0:d={flash}:color=white" if trans == 'flash' else ""
    f.append(f"[{i_b}:v]{sel_v}fps={fps},scale=1080:1920,setsar=1,format=yuv420p{fade_b}[bv]")
    f.append(f"[{i_b}:a]{sel_a}{fmt_a}[ba]")
    partes.append("[bv][ba]")
    f.append(f"{''.join(partes)}concat=n={len(partes)}:v=1:a=1[cv][ca]")
    # --- pele
    if pele == 'limpa':
        f.append("[cv]null[pv]")
        if trans == 'flash':
            sfx.append(('obturador', h))
    elif pele == 'zoom':                            # só o gancho mais fechado; o Reel volta ao quadro original
        zw, zh = int(round(1080 * ZOOM_LIMPO / 2) * 2), int(round(1920 * ZOOM_LIMPO / 2) * 2)
        cx, cy = int((zw - 1080) / 2), int(ZOOM_CY * (ZOOM_LIMPO - 1))
        f.append("[cv]split[z1][z2]")
        f.append(f"[z2]scale={zw}:{zh},crop=1080:1920:{cx}:{cy}[z3]")
        f.append(f"[z1][z3]overlay=0:0:enable='between(t\\,0\\,{h:.3f})',format=yuv420p[pv]")
        if trans == 'flash':
            sfx.append(('obturador', h))
    elif pele == 'texto':                           # caixa de texto nativa só durante o gancho
        pt = os.path.join(tmp, 'texto_nativo.png')
        png_texto_nativo(hk['titulo'], pt)
        i_t = entrada('-loop', '1', '-framerate', str(fps), '-t', f"{h:.3f}", '-i', pt)
        f.append(f"[{i_t}:v]format=rgba[tn]")
        f.append(f"[cv][tn]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[pv]")
        if trans == 'flash':
            sfx.append(('obturador', h))
    elif pele == 'dividida':                        # B-roll em cima, Ana embaixo, legenda na emenda, headline 4 s
        rotulo = os.path.basename(tmp)
        y_ana = int(v['dividida_y'])
        broll = faixa_broll(cfg, v, hk, rotulo, L['bruto'], fps, os.path.join(tmp, 'broll'))
        lista_leg = camada_legenda(pals_na_saida(pals, L), L['bruto'], fps, os.path.join(tmp, 'legenda'))
        # faixa com alfa pré-renderizada em PNG dentro do .mov (sem perda; o qtrle dava 1,5 GB por vídeo e
        # lotou o disco em 13/10, o PNG fica em poucos MB porque quase todo quadro é transparente)
        legenda = os.path.join(tmp, 'legenda', 'legenda.mov')
        ff(['-f', 'concat', '-safe', '0', '-i', lista_leg, '-t', f"{L['bruto']:.3f}", '-vf', f'fps={fps},format=rgba',
            '-c:v', 'png', legenda])
        ph = os.path.join(tmp, 'headline.png')
        png_headline(hk.get('headline') or hk['titulo'], ph)
        i_hl = entrada('-loop', '1', '-framerate', str(fps), '-t', f"{HEADLINE_DUR:.3f}", '-i', ph)
        f.append(f"[cv]crop=1080:{ANA_H}:0:{y_ana}[ana]")
        # B-roll e legenda entram pelo filtro `movie` (dentro do grafo), não como `-i`: com uma entrada a mais
        # lida desde o 0 em paralelo ao corpo, o ffmpeg 8 desta máquina travava (0 % de CPU) quando o concat
        # passava do gancho pro corpo (medido 13/10 no VD009_A, sempre no mesmo quadro)
        f.append(f"movie={filtro_caminho(broll)},fps={fps},setsar=1,format=yuv420p,trim=duration={L['bruto']:.3f},"
                 f"setpts=PTS-STARTPTS[top]")
        f.append("[top][ana]vstack=inputs=2,format=yuv420p[st]")
        f.append(f"movie={filtro_caminho(legenda)},fps={fps},format=rgba,setpts=PTS-STARTPTS[lg]")
        f.append(f"[st][lg]overlay=0:{EMENDA - LEGENDA_H}:eof_action=pass:format=auto,format=yuv420p[st2]")
        f.append(f"[{i_hl}:v]format=rgba,fade=t=out:st={HEADLINE_DUR - 0.2:.3f}:d=0.2:alpha=1[hl]")
        f.append("[st2][hl]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[pv]")
        if trans == 'flash':
            sfx.append(('obturador', h))
    elif pele == 'manchete':
        x, y, w, hh = CARTAO
        pm, pt = os.path.join(tmp, 'mascara.png'), os.path.join(tmp, 'manchete.png')
        png_mascara_cartao(pm)
        png_manchete(hk['titulo'], pt)
        # imagem em loop SEMPRE com duração: loop infinito fazia o orgânico (sem tela final) nunca acabar
        # e travava o filtro do HT VD012_B no mesmo ponto (06/10/2026)
        dur_img = f"{L['bruto']:.3f}"            # exatamente o conteúdo: o alphamerge vai até o fim da entrada mais longa
        i_m = entrada('-loop', '1', '-framerate', str(fps), '-t', dur_img, '-i', pm)
        i_t = entrada('-loop', '1', '-framerate', str(fps), '-t', dur_img, '-i', pt)
        f.append("[cv]split[m1][m2]")
        f.append("[m1]scale=270:480,boxblur=12:2,scale=1080:1920,eq=brightness=-0.32:saturation=0.75[mbg]")
        f.append(f"[m2]scale={w}:{hh},format=yuva420p[mfg0]")
        f.append(f"[{i_m}:v]format=gray[mmk]")
        f.append("[mfg0][mmk]alphamerge[mfg]")
        f.append(f"[mbg][mfg]overlay={x}:{y}:format=auto[mm]")
        f.append(f"[mm][{i_t}:v]overlay=0:0:format=auto:shortest=1,trim=end={L['bruto']:.3f},format=yuv420p[pv]")
        if trans == 'flash':
            sfx.append(('obturador', h))
    elif pele == 'cinema':
        lut = v['lut']
        i_g = entrada('-stream_loop', '-1', '-i', kit['video']['grao_35mm'])
        f.append(f"[cv]split[c1][c2]")
        f.append(f"[c2]lut3d=file='{lut}':interp=tetrahedral,format=yuv420p[c3]")
        f.append(f"[c3][c1]blend=all_mode=normal:all_opacity={LUT_FORCA}[c4]")
        f.append(f"[{i_g}:v]format=yuv420p[gr]")
        # grão só na luma (modo overlay); croma do vídeo intacto (normal com opacidade 1 = camada de cima)
        f.append(f"[c4][gr]blend=c0_mode=overlay:c0_opacity={GRAO_FORCA}:c1_mode=normal:c1_opacity=1:"
                 f"c2_mode=normal:c2_opacity=1:shortest=1,vignette=angle=PI/5,format=yuv420p[pv]")
    else:  # dinamica
        segs = [(0.0, h)]                          # gancho já entra fechado
        for a, b in segmentos_zoom(pals, dur_reel):
            ma, mb = mapa(a), mapa(b)
            if ma is not None and mb is not None and mb - ma > 1.0:
                segs.append((tb + ma, tb + mb))
        expr = '+'.join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in segs)
        zw, zh = int(round(1080 * ZOOM / 2) * 2), int(round(1920 * ZOOM / 2) * 2)
        cx, cy = int((zw - 1080) / 2), int(ZOOM_CY * (ZOOM - 1))
        f.append("[cv]split[z1][z2]")
        f.append(f"[z2]scale={zw}:{zh},crop=1080:1920:{cx}:{cy}[z3]")
        f.append(f"[z1][z3]overlay=0:0:enable='{expr}',format=yuv420p[pv]")
        for a, _b in segs[1:4]:                    # whoosh só nos 3 primeiros zooms (não vira ruído)
            sfx.append(('zoom', a))
    ultimo = "pv"
    # --- efeitos de transição por cima (luma do efeito vira alfa: preto some, luz aparece)
    def efeito(arq, t0, ini_e, dur_e, ganho_alfa, rotulo):
        nonlocal ultimo
        i_e = entrada('-ss', f"{ini_e:.3f}", '-t', f"{dur_e:.3f}", '-i', arq)
        f.append(f"[{i_e}:v]fps={fps},format=rgb24,split[{rotulo}c][{rotulo}l]")
        f.append(f"[{rotulo}l]format=gray,lut=y='min(255\\,val*{ganho_alfa})'[{rotulo}a]")
        f.append(f"[{rotulo}c][{rotulo}a]alphamerge,format=yuva420p,setpts=PTS-STARTPTS+{max(0.0, t0):.3f}/TB[{rotulo}]")
        f.append(f"[{ultimo}][{rotulo}]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[{rotulo}o]")
        ultimo = f"{rotulo}o"
    if trans == 'queima':
        e = EFEITO['queima']
        efeito(kit['video']['queima_cinema'], h - e['pico'], e['ini'], e['dur'], 1.2, 'eq')
        sfx.append(('whoosh', h))
    elif trans == 'glitch':
        e = EFEITO['glitch']
        efeito(kit['video']['glitch'], h - e['pico'], e['ini'], e['dur'], 1.0, 'eg')
        sfx.append(('glitch', h))
    elif trans == 'rebobinar':
        # linhas de ruído de VHS durante a fita voltando, atravessando o corte pro Reel (o "NORMAL TRACKING"
        # ficava gigante e cortado no 9:16; a transição de pontinhos manchava o rosto)
        efeito(kit['video']['vhs_ruido'], h - 0.08, 0.0, r + 0.3, 2.5, 'ev')
        pr = os.path.join(tmp, 'rebobinar.png')
        png_rebobinar(pr)
        i_pr = entrada('-loop', '1', '-framerate', str(fps), '-t', f"{r:.3f}", '-i', pr)
        f.append(f"[{i_pr}:v]format=rgba,setpts=PTS-STARTPTS+{h:.3f}/TB[rbg]")
        f.append(f"[{ultimo}][rbg]overlay=0:0:eof_action=pass:format=auto,format=yuv420p[rbo]")
        ultimo = "rbo"
    # --- destaques de número (motion só onde a fala pede)
    marcadores = ['circulo', 'sublinhado', 'brilho']
    for n_m, m in enumerate(v.get('momentos') or []):
        if m.get('tipo') != 'numero' or not m.get('exibir'):
            continue
        mt = mapa(m['inicio'])
        if mt is None:
            continue
        t_out = tb + mt
        pasta = os.path.join(tmp, f'destaque_{n_m}')
        if os.path.isdir(pasta):
            shutil.rmtree(pasta)
        tipo = m.get('marcador') or marcadores[n_m % len(marcadores)]
        gerar_destaque(m['exibir'], tipo, pasta, kit, fps, m.get('linha2'), m.get('tam_max', 168))
        cy_m = m.get('cy', DESTAQUE_CY)          # altura escolhida no enquadramento de cada Reel
        i_d = entrada('-framerate', str(fps), '-i', os.path.join(pasta, 'q%03d.png'))
        if pele == 'manchete':                     # dentro do cartão, acima da legenda queimada
            esc, px, py = 0.85, int((1080 - 1080 * 0.85) / 2), int(CARTAO[1] + cy_m * 0.8 - 560 * 0.85 / 2)
            f.append(f"[{i_d}:v]format=rgba,scale={int(1080*esc)}:{int(560*esc)},setpts=PTS-STARTPTS+{t_out:.3f}/TB[d{n_m}]")
        else:
            px, py = 0, cy_m - 280
            f.append(f"[{i_d}:v]format=rgba,setpts=PTS-STARTPTS+{t_out:.3f}/TB[d{n_m}]")
        f.append(f"[{ultimo}][d{n_m}]overlay={px}:{py}:eof_action=pass:format=auto,format=yuv420p[d{n_m}o]")
        ultimo = f"d{n_m}o"
        sfx.append(('impacto', t_out + 0.06))
    # --- áudio: voz + SFX alinhados pelo pico
    mix = ["[ca]"]
    for n_s, (nome, t_pico) in enumerate(sfx):
        if nome not in kit['sfx']:
            print(f'   aviso: SFX "{nome}" fora do kit ({KIT_JSON}); seguindo sem ele')
            continue
        s = kit['sfx'][nome]
        atraso = t_pico - s['pico']
        ini_s, dur_s = s['ini'], s['dur']
        if nome == 'rebobinar':
            dur_s = r + 0.12
        if atraso < 0:
            ini_s, dur_s, atraso = ini_s - atraso, dur_s + atraso, 0.0
        i_s = entrada('-ss', f"{ini_s:.3f}", '-t', f"{dur_s:.3f}", '-i', s['arq'])
        ms = int(round(atraso * 1000))
        f.append(f"[{i_s}:a]{fmt_a},volume={s['ganho']}dB,afade=t=out:st={max(0.0, dur_s - 0.08):.3f}:d=0.08,"
                 f"adelay=delays={ms}|{ms}:all=1[s{n_s}]")
        mix.append(f"[s{n_s}]")
    f.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[mv]")
    # --- fim: tela final (anúncio) ou aceleração (orgânico)
    bruto, total, fator = L['bruto'], L['total'], L['fator']
    if L['com_tela']:
        i_t = entrada('-i', cfg['tela_final'])
        pausa, fusao, fade_voz = cfg['pausa'], cfg['fusao'], cfg['fade_voz']
        pad_tela = f",tpad=start_duration={pausa}:start_mode=clone" if pausa > 0 else ""
        f.append(f"[{i_t}:v]fps={fps},scale=1080:1920,setsar=1,format=yuv420p{pad_tela},settb=AVTB[tv]")
        f.append(f"[{ultimo}]settb=AVTB[cv2]")
        f.append(f"[cv2][tv]xfade=transition=fade:duration={fusao}:offset={bruto - fusao:.3f}[vout]")
        f.append(f"[mv]afade=t=out:st={bruto - fade_voz:.3f}:d={fade_voz},apad=whole_dur={total:.3f},"
                 f"alimiter=limit=0.89:level=false[aout]")
    elif fator > 1.0:
        f.append(f"[{ultimo}]setpts=PTS/{fator},fps={fps}[vout]")
        f.append(f"[mv]afade=t=out:st={bruto - 0.03:.3f}:d=0.03,atempo={fator},alimiter=limit=0.89:level=false[aout]")
    else:
        f.append(f"[{ultimo}]null[vout]")
        f.append(f"[mv]afade=t=out:st={bruto - 0.03:.3f}:d=0.03,alimiter=limit=0.89:level=false[aout]")
    saida_args = []
    if trecho:
        saida_args = ['-ss', f"{trecho[0]:.3f}", '-t', f"{trecho[1] - trecho[0]:.3f}"]
    elif ate:
        saida_args = ['-t', f"{ate:.3f}"]
    else:
        saida_args = ['-t', f"{total + 0.2:.3f}"]          # teto: nenhuma saída passa da duração prevista
    parte = saida + '.part.mp4'
    with open(os.path.join(tmp, 'filtro.txt'), 'w', encoding='utf-8') as fp:   # pra depurar sem reconstruir
        fp.write(';\n'.join(f))
    ff([*entradas, '-filter_complex', ';'.join(f), '-map', '[vout]', '-map', '[aout]', *saida_args,
        *(['-c:v', 'libx265', '-preset', preset, '-crf', '20', '-tag:v', 'hvc1', '-x265-params', 'log-level=error']
          if codec == 'hevc' else ['-c:v', 'libx264', '-preset', preset, '-crf', '18']),
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', parte])
    os.replace(parte, saida)
    for sub in ('broll', 'legenda'):               # temporários da tela dividida: refeitos se precisar renderizar de novo
        shutil.rmtree(os.path.join(tmp, sub), ignore_errors=True)
    return L, fps

def conferencia(saida, L, momentos_t, destino_png):
    """5 quadros: abertura, fim do gancho, início do Reel, 1º destaque (ou +5 s), último quadro."""
    tot = duracao(saida)
    pts = [0.4, max(0.0, L['h'] - 0.15), min(tot - 0.2, L['tb'] + 0.4),
           min(tot - 0.2, (momentos_t[0] + 0.7) if momentos_t else L['tb'] + 5), max(0.0, tot - 0.1)]
    ent = []
    for t in pts[:-1]:
        ent += ['-ss', f"{t / L['fator']:.3f}", '-i', saida]
    # último quadro contado do FIM: no orgânico o áudio pode acabar depois do vídeo e um -ss perto do fim
    # do arquivo não acha quadro nenhum (o ffmpeg sai com 0 sem gravar o PNG)
    ent += ['-sseof', '-0.3', '-i', saida]
    filtro = "".join(f"[{i}:v]scale=216:384[f{i}];" for i in range(5)) + "[f0][f1][f2][f3][f4]hstack=inputs=5"
    ff([*ent, '-filter_complex', filtro, '-frames:v', '1', destino_png])
    if not os.path.exists(destino_png):
        raise RuntimeError(f'prancha de conferência não foi gravada: {destino_png}')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('plano')
    ap.add_argument('--so', help='só esta saída, ex.: VD004_A')
    ap.add_argument('--versao', help='só esta versão (HT ou ORG)')
    ap.add_argument('--forcar', action='store_true')
    ap.add_argument('--ate', type=float, help='prévia: só os primeiros N s')
    ap.add_argument('--trecho', type=float, nargs=2, help='prévia: só de A a B s')
    ap.add_argument('--amostra', action='store_true', help='grava em _amostra/ (não mexe nas saídas finais)')
    ap.add_argument('--preset', default='medium', help='preset do x264 (mesmo CRF 18 = mesma qualidade; veryslow comprime mais)')
    ap.add_argument('--codec', default='h264', choices=['h264', 'hevc'],
                    help='hevc = x265 CRF 20 (qualidade igual ou maior que x264 CRF 18, ~35%% menor; o Instagram aceita)')
    a = ap.parse_args()
    cfg = json.load(open(a.plano, encoding='utf-8'))
    # kit de efeitos/SFX é opcional pros estilos clean e dividida (sem ele, sai sem o som do obturador)
    kit = json.load(open(KIT_JSON, encoding='utf-8')) if os.path.exists(KIT_JSON) else {'sfx': {}, 'video': {}}
    destino = cfg['destino']
    acel_max = cfg.get('acel_max', 1.08)
    pasta_conf = os.path.join(destino, '_conferencia')
    os.makedirs(pasta_conf, exist_ok=True)
    relatorio = []
    for v in cfg['videos']:
        pals = palavras(v['transcricao'])
        dur_reel = duracao(v['arquivo'])
        for letra, hk in sorted(v['hooks'].items()):
            rotulo = f"{v['vd']}_{letra}"
            if a.so and a.so != rotulo:
                continue
            for var in variantes_do(cfg):
                if a.versao and var.get('id') != a.versao:
                    continue
                chave = f"{var['id']} {rotulo}"
                tmp = os.path.join(destino, '_peles_tmp', f"{var['id']}_{rotulo}")
                if a.amostra:
                    pasta = os.path.join(destino, '_amostra')
                    sufixo = f"_{a.trecho[0]:.0f}-{a.trecho[1]:.0f}s" if a.trecho else (f"_{a.ate:.0f}s" if a.ate else "")
                    final = os.path.join(pasta, f"{var['id']} {rotulo} {hk['pele']}-{hk['abertura']}{sufixo}.mp4")
                else:
                    pasta = os.path.join(destino, var.get('subpasta', ''))
                    final = os.path.join(pasta, f"{var['prefixo']}{rotulo}.mp4")
                    propria = os.path.join(pasta, f"{var['prefixo']}{rotulo}")
                    if os.path.isdir(propria):
                        final = os.path.join(propria, f"{var['prefixo']}{rotulo}.mp4")
                os.makedirs(os.path.dirname(final), exist_ok=True)
                L = planejar(cfg, v, hk, var, pals, dur_reel, fps_de(v['arquivo']))
                base = {'saida': chave, 'versao': var['id'], 'reel': v['shortcode'], 'pele': hk['pele'],
                        'abertura': hk['abertura'], 'titulo': hk.get('titulo'), 'gancho': hk['texto'],
                        'corte_ini': L['ini'], 'corte_fim': L['fim'], 'dur_gancho': L['h'],
                        'cortes_versao_curta': L['cortes'], 'aceleracao': L['fator'], 'dur_prevista': round(L['total'], 2)}
                if not a.forcar and not a.amostra and os.path.exists(final):
                    try:
                        if abs(duracao(final) - L['total']) <= 0.15:
                            relatorio.append({**base, 'arquivo': os.path.relpath(final, destino), 'dur_real': round(duracao(final), 2),
                                              'alertas': ['ACELERAÇÃO ACIMA DO TETO'] if L['fator'] > acel_max else []})
                            print(f"   [{chave}] já pronto, pulando")
                            continue
                    except Exception:
                        pass
                    print(f"   [{chave}] arquivo existente incompleto, refazendo")
                L, fps = render(cfg, v, hk, var, pals, final, tmp, kit, ate=a.ate, trecho=a.trecho, preset=a.preset, codec=a.codec)
                if a.amostra:
                    print(f"   [{chave}] amostra {os.path.basename(final)}  {duracao(final):.2f}s")
                    continue
                dur_real = duracao(final)
                mts = [L['tb'] + L['mapa'](m['inicio']) for m in v.get('momentos') or []
                       if m.get('tipo') == 'numero' and m.get('exibir') and L['mapa'](m['inicio']) is not None]
                png = os.path.join(pasta_conf, f"{var['id']}_{rotulo}.png")
                conferencia(final, L, mts, png)
                l0 = luma_frame(final, t=0)
                alerta = []
                if l0 is None or l0 < 25: alerta.append('ABRE NO PRETO')
                if abs(dur_real - L['total']) > 0.15: alerta.append('DURAÇÃO NÃO FECHA')
                if L['fator'] > acel_max: alerta.append('ACELERAÇÃO ACIMA DO TETO')
                if var.get('dur_max') and dur_real > var['dur_max'] + 0.1: alerta.append('PASSOU DO LIMITE')
                relatorio.append({**base, 'arquivo': os.path.relpath(final, destino), 'dur_real': round(dur_real, 2),
                                  'luma_frame0': l0, 'destaques': len(mts), 'alertas': alerta})
                acel = f"  acel {L['fator']:.3f}×" if L['fator'] > 1 else ""
                curta = f"  curta -{sum(b - a for a, b in L['cortes']):.1f}s" if L['cortes'] else ""
                print(f"   [{chave}] {hk['pele']}/{hk['abertura']}  {dur_real:.2f}s (previsto {L['total']:.2f}){acel}{curta}  "
                      f"luma0 {l0}  {' '.join(alerta) or 'ok'}")
    if not a.amostra:
        sufixo = "_".join(x for x in (a.so, a.versao) if x)
        nome_rel = f"_relatorio_{sufixo}.json" if sufixo else "_relatorio.json"
        with open(os.path.join(destino, nome_rel), 'w', encoding='utf-8') as fp:
            json.dump(relatorio, fp, ensure_ascii=False, indent=2)
        print(f"== {len(relatorio)} saídas; relatório em {destino}/{nome_rel}")

if __name__ == '__main__':
    main()
