# clean-links-bot
Bot to clean up twitter and youtube links from Telegram Groups and Channels

# Setup
```bash
git clone https://github.com/Dwadziescia-Jeden/clean-links-bot
cd clean-links-bot
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```
# Run tests
`python -m pytest`

# Bot Configuration
* Create the bot with Telegram's [@BotFather](https://web.telegram.org/a/#93372553)
* Configure the bot to disable privacy. This makes the bot receive other people's messages. We do not store them, though.
* Add the bot to a group or channel. Note: A version of the bot which would delete OP messages would required admin privileges.
* Get the token and try it out

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF-your-real-token"
python clean_links_bot.py
```
If it works, install.

# Installation as a service
```bash
sudo useradd -r -s /bin/false bot
sudo chown -R bot:bot ~/clean-links-bot
echo TELEGRAM_BOT_TOKEN=123456:ABC-DEF-your-real-token | sudo tee /etc/clean-links-bot.env > /dev/null
sudo chmod 600 /etc/clean-links-bot.env

sudo cp contrib/clean-links-bot.service /etc/systemd/system
sudo systemctl daemon-reload
sudo systemctl enable clean-links-bot.service
sudo systemctl start clean-links-bot.service

journalctl -u clean-links-bot.service -f
```
