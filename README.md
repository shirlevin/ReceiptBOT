# Receipt OCR Telegram Bot

A Telegram bot that processes receipt images using OCR, extracts key information (company, date, amount), and stores the data in a PostgreSQL database.

## Features

- OCR processing of receipt images
- Automatic data extraction (company name, date, total amount)
- Interactive prompts for missing information
- PostgreSQL database storage
- Multi-user support with individual receipt histories
- Hebrew and English language support

## Quick Start

### Prerequisites
- Python 3.7+
- PostgreSQL database
- Telegram Bot Token from [@BotFather](https://t.me/botfather)

### Installation

1. **Clone and setup:**
   ```bash
   git clone https://github.com/yourusername/receipt-bot.git
   cd receipt-bot
   pip install -r requirements.txt
   ```

2. **Configure environment:**
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` with your credentials:
   ```env
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token
   DB_HOST=your_database_host
   DB_PASSWORD=your_database_password
   DB_USER=postgres
   DB_NAME=telegramdb
   ```

3. **Setup database:**
   ```python
   python setup_database.py
   ```

4. **Run the bot:**
   ```bash
   python main.py
   ```

## Usage

1. Send `/start` to the bot
2. Upload a receipt image
3. Review extracted data
4. Complete any missing information when prompted
5. Use `/payments` to view your receipt history

### Commands
- `/start` - Start the bot
- `/help` - Usage instructions
- `/payments` - View saved receipts
- `/raw` - Show raw OCR text

## Database Schema

```sql
CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(20) NOT NULL,
    company VARCHAR(255) NOT NULL,
    date DATE NOT NULL,
    price DECIMAL(10, 2) NOT NULL
);
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | Telegram bot token |
| `DB_HOST` | Yes | PostgreSQL host |
| `DB_USER` | Yes | Database username |
| `DB_PASSWORD` | Yes | Database password |
| `DB_NAME` | Yes | Database name |
| `OPENAI_API_KEY` | No | OpenAI API key for enhanced OCR |

## Dependencies

- `python-telegram-bot` - Telegram Bot API
- `psycopg2-binary` - PostgreSQL adapter
- `python-dotenv` - Environment variables
- `Pillow` - Image processing
- `pytesseract` - OCR functionality

## Troubleshooting

**Bot not responding:**
- Verify bot token in `.env`
- Check if bot process is running

**Database connection issues:**
- Confirm PostgreSQL is running
- Verify database credentials
- Ensure database and table exist

**OCR problems:**
- Install Tesseract: `brew install tesseract` (Mac) or `apt install tesseract-ocr` (Linux)
- Ensure good image quality and lighting


**Important:** Keep your `.env` file secure and never share API keys.