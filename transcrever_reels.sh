#!/bin/zsh
# Transcreve os Reels baixados com ElevenLabs Scribe v2 e monta o corpus de fala da Ana.
# Reaproveita o transcrever_scribe.sh (chave do Keychain, nunca impressa) e o banco de grafias da VSL.
# Pode rodar de novo: pula o que já foi transcrito.
# uso: zsh transcrever_reels.sh
set -u
source "${0:A:h}/config.sh"
base="$REELS_BASE"
pipeline="$PROJETO/scribe"
mkdir -p "$base/audio" "$base/transcricoes"

videos=("$base/videos"/*.mp4(N))
(( ${#videos} )) || { echo "nenhum vídeo em $base/videos (rodar o baixar_reels.sh antes)"; exit 1; }

feitos=0
for video in "${videos[@]}"; do
  nome="${video:t:r}"
  saida="$base/transcricoes/$nome.json"
  if [[ -s "$saida" ]] && grep -q '"text"' "$saida"; then
    echo "já transcrito: $nome"; (( feitos++ )); continue
  fi
  # áudio mono 16 kHz: o Scribe não precisa de mais que isso e o upload fica leve
  ffmpeg -nostdin -loglevel error -y -i "$video" -vn -ac 1 -ar 16000 "$base/audio/$nome.wav" || { echo "ffmpeg falhou: $nome"; continue; }
  echo "== $nome"
  zsh "$pipeline/transcrever_scribe.sh" "$base/audio/$nome.wav" "$saida"   # vocab.txt (local) ou vocab.exemplo.txt
  # transcrição ok = o WAV não serve mais (é refeito se precisar transcrever de novo)
  grep -q '"text"' "$saida" && { (( feitos++ )); rm -f "$base/audio/$nome.wav" "$base/audio/._$nome.wav"; }
  sleep 1
done
echo "\n$feitos de ${#videos} transcritos"
python3 "${0:A:h}/montar_corpus.py"
