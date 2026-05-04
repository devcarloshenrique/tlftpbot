# Instalação — Python Manual

Guia para rodar o TLFTPBot sem Docker, usando Python diretamente.

---

## Requisitos

- Python 3.10+
- MongoDB 5.0+ (local ou Atlas)
- Git

---

## Linux (Ubuntu/Debian)

### 1. Instalar Dependências

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git
```

### 2. MongoDB

**Opção A — MongoDB Atlas (recomendado)**

Crie um cluster gratuito em [cloud.mongodb.com](https://cloud.mongodb.com) e copie a connection string.

**Opção B — MongoDB Local**

```bash
sudo apt install -y mongodb-org
sudo systemctl enable --now mongod
```

### 3. Clonar e Instalar

```bash
git clone https://github.com/devcarloshenrique/tlftpbot.git
cd tlftpbot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Configurar .env

```bash
cp .env.example .env
nano .env
```

Preencha:

```env
API_ID=12345678
API_HASH=abc123def456789...
BOT_TOKEN=1234567890:AABBccDDee...
CHAT_ID=-1001234567890

# Para MongoDB Atlas:
MONGODB_CLOUD_URI=mongodb+srv://user:pass@cluster.mongodb.net/?appName=myapp

# Para MongoDB local, deixe vazio e configure a variável MONGODB diretamente:
# (o fallback via docker não se aplica fora do compose)
```

> **Nota**: Fora do Docker Compose, o fallback automático não funciona. Use `MONGODB=mongodb://localhost:27017` diretamente no `.env` ou configure `MONGODB_CLOUD_URI` com a string Atlas.

### 5. Criar Usuário e Iniciar

```bash
python accounts_manager.py
python main.py
```

---

## Windows

1. Instale Python 3.11+ de [python.org](https://python.org) (marque "Add Python to PATH")
2. Instale Git de [git-scm.com](https://git-scm.com/download/win)
3. Use MongoDB Atlas (mais simples que instalar local)

```powershell
cd C:\
git clone https://github.com/devcarloshenrique/tlftpbot.git
cd tlftpbot
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edite .env no Bloco de Notas
python accounts_manager.py
python main.py
```

---

## macOS

```bash
brew install python@3.11 mongodb-community git
brew services start mongodb-community
git clone https://github.com/devcarloshenrique/tlftpbot.git
cd tlftpbot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
python accounts_manager.py
python main.py
```

---

## Systemd Service (Linux)

```bash
sudo nano /etc/systemd/system/tlftpbot.service
```

```ini
[Unit]
Description=TLFTPBot Server
After=network.target

[Service]
Type=simple
User=seu_usuario
WorkingDirectory=/opt/tlftpbot
Environment="PATH=/opt/tlftpbot/venv/bin"
ExecStart=/opt/tlftpbot/venv/bin/python /opt/tlftpbot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now tlftpbot
sudo journalctl -u tlftpbot -f
```

---

## Solução de Problemas

### "ModuleNotFoundError"

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### "Connection refused"

- Servidor não está rodando
- Firewall bloqueando porta 2121
- Porta em uso: `sudo netstat -tulpn | grep 2121`