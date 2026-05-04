# TLFTPBot

**Servidor FTP com Telegram como backend de armazenamento ilimitado.**

---

## Início Rápido (Docker)

```bash
git clone https://github.com/devcarloshenrique/tlftpbot.git
cd tlftpbot
cp .env.example .env
# Edite .env com suas credenciais (API_ID, API_HASH, BOT_TOKEN, CHAT_ID)
```

### Modo Nuvem (MongoDB Atlas)

Deixe `MONGODB_CLOUD_URI` preenchido no `.env`:

```bash
docker compose up -d
```

### Modo Local (MongoDB no container)

Deixe `MONGODB_CLOUD_URI=` vazio no `.env`:

```bash
docker compose --profile local up -d
```

O servidor FTP estará disponível em `localhost:2121`.

---

## Configuração (.env)

| Variável | Descrição |
|----------|-----------|
| `API_ID` | Obtido em [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | Obtido em [my.telegram.org](https://my.telegram.org) |
| `BOT_TOKEN` | Criado com [@BotFather](https://t.me/BotFather) |
| `CHAT_ID` | ID do canal privado (use [@userinfobot](https://t.me/userinfobot)) |
| `MONGODB_CLOUD_URI` | Vazio = MongoDB local. Preenchido = MongoDB Atlas |
| `HOST` | Host do servidor FTP (padrão: `0.0.0.0`) |
| `PORT` | Porta FTP (padrão: `2121`) |
| `MAX_WORKERS` | Workers simultâneos (padrão: `4`) |
| `CHUNK_SIZE_MB` | Tamanho dos chunks em MB (padrão: `64`) |

---

## Criar Usuário FTP

```bash
docker exec -it nebulaftp python accounts_manager.py
```

---

## Conectar via Cliente FTP

| Campo | Valor |
|-------|-------|
| Host | IP do servidor |
| Porta | `2121` |
| Usuário | Criado acima |
| Senha | Definida acima |

---

## Subprojetos

Cada subprojeto tem seu próprio `docker-compose.yml` e `.env` local com `MONGODB_CLOUD_URI`:

### API REST (Node.js)

```bash
cd api
cp ../.env.example .env  # ou configure manualmente
docker compose up -d       # API em localhost:3000
```

### TL-Stream (Streaming HTTP)

```bash
cd TL-Stream/streaming
cp .env.example .env      # e configure MONGODB_CLOUD_URI
docker compose up -d       # Streaming em localhost:8000
```

---

## Estrutura do Projeto

```
tlftpbot/
├── docker-compose.yml      # Serviço FTP + MongoDB local (profile "local")
├── Dockerfile              # Imagem do servidor FTP
├── main.py                 # Entrypoint
├── accounts_manager.py     # Gerenciador de usuários FTP
├── .env.example            # Template de configuração
├── api/                    # API REST (Node.js)
│   ├── docker-compose.yml
│   └── .env
├── TL-Stream/              # Streaming HTTP (FastAPI + Pyrogram)
│   └── streaming/
│       ├── docker-compose.yml
│       └── .env
├── ftp/                    # Módulo do servidor FTP
├── staging/                # Cache de uploads
├── logs/                   # Logs persistentes
└── docs/                   # Documentação
```

---

## Comandos Úteis

```bash
docker compose logs -f app        # Logs em tempo real (FTP)
docker compose restart            # Reiniciar FTP
docker compose down               # Parar containers
```

---

## Docs

- [Configurar Telegram](docs/TELEGRAM_SETUP.md)
- [Instalação Docker](docs/DOCKER.md)
- [Instalação Python](docs/INSTALLATION.md)
- [TL-Stream (Streaming)](docs/TL-STREAM.md)