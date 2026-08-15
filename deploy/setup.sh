#!/bin/bash
# Plataforma de Notas e Frequência — One-shot Bootstrap Script for Debian 12
set -e

echo "=== Iniciando Instalação da Plataforma de Notas e Frequência ==="

# 1. Update system packages
sudo apt update
# sudo apt upgrade -y

# 2. Install dependencies (Python, SQLite, WeasyPrint system libs for PDF generation)
sudo apt install -y python3 python3-pip python3-venv sqlite3 \
  build-essential python3-dev libpango-1.0-0 libpangoft2-1.0-0 \
  libjpeg-dev zlib1g-dev libopenjp2-7-dev gconf-service libasound2 \
  libatk1.0-0 libc6 libcairo2 libcups2 libdbus-1-3 libexpat1 \
  libfontconfig1 libgcc1 libgdk-pixbuf2.0-0 libglib2.0-0 libgtk-3-0 \
  libnspr4 libpango-1.0-0 libpangocairo-1.0-0 libstdc++6 libx11-6 \
  libx11-xcb1 libxcb1 libxcomposite1 libxcursor1 libxdamage1 \
  libxext6 libxfixes3 libxi6 libxrandr2 libxrender1 libxss1 \
  libxtst6 ca-certificates fonts-liberation libappindicator1 \
  libnss3 lsb-release xdg-utils curl

# 3. Create app workspace
APP_DIR="/opt/chamada-provas"
sudo mkdir -p "$APP_DIR"
sudo chown -R $USER:$USER "$APP_DIR"

# Copy files to directory (Assuming git clone / transfer already did this)
# Build Python Virtual Environment
echo "Criando ambiente virtual Python..."
python3 -m venv "$APP_DIR/venv"
source "$APP_DIR/venv/bin/activate"

# Install requirements
echo "Instalando dependências Python..."
pip install --upgrade pip
pip install -r "$APP_DIR/requirements.txt"

# 4. Systemd configuration
echo "Configurando serviço Systemd..."
sudo cp "$APP_DIR/deploy/chamada-provas.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable chamada-provas
sudo systemctl start chamada-provas

echo "=== Instalação concluída com sucesso! ==="
echo "Por favor, verifique o arquivo: /opt/chamada-provas/instance/first_run_credentials.txt para obter a senha de administrador gerada."

