# Guia de Instalação — Docker

Guia para rodar o TLFTPBot via Docker Compose no Ubuntu/Debian.

---

## Pré-requisitos

1. **API_ID e API_HASH**: [my.telegram.org](https://my.telegram.org)
2. **BOT_TOKEN**: Criar com [@BotFather](https://t.me/BotFather)
3. **CHAT_ID**: Canal privado com o bot como admin, ID obtido via [@userinfobot](https://t.me/userinfobot)

---

## Passo 1: Instalar Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
rm get-docker.sh
sudo systemctl enable --now docker
```

Adicionar usuário ao grupo Docker (opcional):

```bash
sudo usermod -aG docker $USER
newgrp docker
```

---

## Passo 2: Clonar e Configurar

```bash
git clone https://github.com/devcarloshenrique/tlftpbot.git
cd tlftpbot
cp .env.example .env
nano .env
```

Preencha suas credenciais:

```env
API_ID=12345678
API_HASH=abc123def456789...
BOT_TOKEN=1234567890:AABBccDDeeFFgg...
CHAT_ID=-1001234567890
MONGODB_CLOUD_URI=
```

---

## Passo 3: Escolher Modo MongoDB

### Nuvem (MongoDB Atlas)

Preencha `MONGODB_CLOUD_URI` com a string do Atlas:

```env
MONGODB_CLOUD_URI=mongodb+srv://user:pass@cluster.mongodb.net/?appName=myapp
```

```bash
docker compose up -d
```

### Local (MongoDB no container)

Deixe `MONGODB_CLOUD_URI=` vazio:

```env
MONGODB_CLOUD_URI=
```

```bash
docker compose --profile local up -d
```

---

## Passo 4: Verificar e Criar Usuário

```bash
docker compose ps
docker compose logs -f app
```

Criar usuário FTP:

```bash
docker exec -it nebulaftp python accounts_manager.py
```

---

## Passo 5: Conectar via Cliente FTP

Abra o FileZilla (ou outro cliente):

| Campo | Valor |
|-------|-------|
| Host | IP do servidor |
| Porta | `2121` |
| Usuário | Criado no passo 4 |
| Senha | Definida no passo 4 |
| Modo | Passivo (recomendado) |

---

## Firewall

```bash
sudo ufw allow 2121/tcp
sudo ufw allow 60000:60100/tcp
sudo ufw reload
```

---

## Comandos Úteis

```bash
docker compose restart            # Reiniciar servidor
docker compose logs -f app        # Logs em tempo real
docker compose down               # Parar containers
docker compose --profile local down  # Parar (modo local)
git pull && docker compose build  # Atualizar código
```

---

## Problemas Comuns

### "Connection refused"

- Verificar `docker compose ps`
- Verificar firewall (`sudo ufw status`)

### "Peer id invalid"

- Adicionar o bot como **admin** no canal com **todas as permissões**

### Container reiniciando

```bash
docker compose logs app
# Verificar API_ID, API_HASH, BOT_TOKEN e CHAT_ID