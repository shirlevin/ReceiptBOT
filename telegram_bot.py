import logging
import tempfile
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
import re

from receipt_ocr import process_receipt

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN found. Please set it in your .env file or environment variables.")

# Database configuration
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'telegramdb')
}


# Database connection function
def get_db_connection():
    """Create and return a database connection."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except psycopg2.Error as e:
        logger.error(f"Database connection error: {e}")
        return None


def insert_payment(user_id, company, date, price):
    """Insert a payment record into the database."""
    conn = get_db_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        insert_query = """
        INSERT INTO payments (user_id, company, date, price)
        VALUES (%s, %s, %s, %s)
        RETURNING id;
        """
        cursor.execute(insert_query, (str(user_id), company, date, price))
        payment_id = cursor.fetchone()[0]
        conn.commit()
        logger.info(f"Payment inserted successfully with ID: {payment_id}")
        return True
    except psycopg2.Error as e:
        logger.error(f"Error inserting payment: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def get_user_payments(user_id):
    """Get all payments for a specific user."""
    conn = get_db_connection()
    if not conn:
        return []

    try:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("""
            SELECT id, company, date, price 
            FROM payments 
            WHERE user_id = %s 
            ORDER BY date DESC
        """, (str(user_id),))
        payments = cursor.fetchall()
        return payments
    except psycopg2.Error as e:
        logger.error(f"Error fetching user payments: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def parse_price(price_str):
    """Extract numeric price from string."""
    if not price_str or price_str == 'לא נמצא':
        return None

    # Remove currency symbols and extract numbers
    price_clean = re.sub(r'[^\d.,]', '', str(price_str))
    if not price_clean:
        return None

    try:
        # Handle different decimal separators
        if ',' in price_clean and '.' in price_clean:
            # Assume comma is thousands separator
            price_clean = price_clean.replace(',', '')
        elif ',' in price_clean:
            # Assume comma is decimal separator
            price_clean = price_clean.replace(',', '.')

        return float(price_clean)
    except ValueError:
        return None


def parse_date(date_str):
    """Parse date string to datetime.date object."""
    if not date_str or date_str == 'לא נמצא':
        return datetime.now().date()

    # Common date formats
    date_formats = [
        '%d/%m/%Y',
        '%d.%m.%Y',
        '%d-%m-%Y',
        '%Y-%m-%d',
        '%d/%m/%y',
        '%d.%m.%y'
    ]

    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    # If no format matches, return today's date
    return datetime.now().date()


def get_missing_data_text(missing_field):
    """Get the prompt text for missing data."""
    prompts = {
        'company': "🏢 **אנא הכנס את שם העסק/החברה:**\n(לדוגמה: סופר פארם, מקדונלדס, רמי לוי)",
        'price': "💰 **אנא הכנס את הסכום:**\n(לדוגמה: 25.50, 100, 15.99)",
        'date': "📅 **אנא הכנס את התאריך:**\n(לדוגמה: 09/07/2024, 9.7.24, היום)"
    }
    return prompts.get(missing_field, "אנא הכנס את המידע החסר:")


def validate_and_parse_input(field_type, user_input):
    """Validate and parse user input for missing fields."""
    if field_type == 'company':
        if len(user_input.strip()) >= 2:
            return user_input.strip(), True
        else:
            return None, False

    elif field_type == 'price':
        parsed_price = parse_price(user_input)
        if parsed_price and parsed_price > 0:
            return parsed_price, True
        else:
            return None, False

    elif field_type == 'date':
        if user_input.lower().strip() in ['היום', 'today']:
            return datetime.now().date(), True
        else:
            parsed_date = parse_date(user_input)
            if parsed_date:
                return parsed_date, True
            else:
                return None, False

    return None, False


async def process_missing_data_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process user input for missing data."""
    waiting_for = context.user_data.get('waiting_for')
    pending_receipt = context.user_data.get('pending_receipt')

    if not waiting_for or not pending_receipt:
        return False

    user_input = update.message.text.strip()
    parsed_value, is_valid = validate_and_parse_input(waiting_for, user_input)

    if not is_valid:
        error_messages = {
            'company': "❌ שם העסק צריך להכיל לפחות 2 תווים. נסה שוב:",
            'price': "❌ הסכום לא תקין. הכנס מספר (לדוגמה: 25.50). נסה שוב:",
            'date': "❌ התאריך לא תקין. השתמש בפורמט כמו 09/07/2024 או כתוב 'היום'. נסה שוב:"
        }
        await update.message.reply_text(error_messages.get(waiting_for, "❌ קלט לא תקין. נסה שוב:"))
        return True

    # Update the pending receipt data
    if waiting_for == 'company':
        pending_receipt['company'] = parsed_value
    elif waiting_for == 'price':
        pending_receipt['parsed_price'] = parsed_value
        pending_receipt['price'] = str(parsed_value)
    elif waiting_for == 'date':
        pending_receipt['parsed_date'] = parsed_value
        pending_receipt['date'] = parsed_value.strftime('%d/%m/%Y')

    # Remove the current field from missing data
    pending_receipt['missing_data'].remove(waiting_for)

    # Check if there's more missing data
    if pending_receipt['missing_data']:
        next_missing = pending_receipt['missing_data'][0]
        context.user_data['waiting_for'] = next_missing

        # Confirm current input and ask for next
        confirm_text = f"✅ {get_field_display_name(waiting_for)}: {get_display_value(waiting_for, parsed_value)}\n\n"
        missing_text = get_missing_data_text(next_missing)

        await update.message.reply_text(f"{confirm_text}{missing_text}")
    else:
        # All data collected, save to database
        receipt = pending_receipt
        success = insert_payment(
            receipt['user_id'],
            receipt['company'],
            receipt['parsed_date'],
            receipt['parsed_price']
        )

        if success:
            db_status = "✅ **הנתונים נשמרו בהצלחה!**"
        else:
            db_status = "⚠️ **שגיאה בשמירת הנתונים**"

        final_response = (
            "🎉 **פרטי הקבלה המלאים:**\n\n"
            f"🏢 **עסק:** {receipt['company']}\n"
            f"📅 **תאריך:** {receipt['date']}\n"
            f"💰 **סכום:** {receipt['parsed_price']:.2f} ₪\n\n"
            f"{db_status}"
        )

        await update.message.reply_text(final_response, parse_mode='Markdown')

        # Clear the context
        context.user_data.pop('waiting_for', None)
        context.user_data.pop('pending_receipt', None)

    return True


def get_field_display_name(field_type):
    """Get display name for field type."""
    names = {
        'company': 'עסק',
        'price': 'סכום',
        'date': 'תאריך'
    }
    return names.get(field_type, field_type)


def get_display_value(field_type, value):
    """Get formatted display value for field."""
    if field_type == 'price':
        return f"{value:.2f} ₪"
    elif field_type == 'date':
        if isinstance(value, datetime):
            return value.strftime('%d/%m/%Y')
        return str(value)
    else:
        return str(value)
    """Parse date string to datetime.date object."""
    if not date_str or date_str == 'לא נמצא':
        return datetime.now().date()

    # Common date formats
    date_formats = [
        '%d/%m/%Y',
        '%d.%m.%Y',
        '%d-%m-%Y',
        '%Y-%m-%d',
        '%d/%m/%y',
        '%d.%m.%y'
    ]

    for fmt in date_formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    # If no format matches, return today's date
    return datetime.now().date()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    welcome_message = (
        "שלום! אני בוט לסריקת קבלות 🧾\n\n"
        "שלח לי תמונה של קבלה ואני אחלץ:\n"
        "• שם החברה/עסק\n"
        "• תאריך\n"
        "• סכום כולל\n\n"
        "הנתונים יישמרו באופן אוטומטי במסד הנתונים שלך!\n\n"
        "פקודות זמינות:\n"
        "/start - התחל\n"
        "/help - עזרה\n"
        "/payments - הצג את כל התשלומים שלי\n"
        "/raw - הצג טקסט גולמי מהקבלה האחרונה\n\n"
        "פשוט שלח תמונה של הקבלה!"
    )
    await update.message.reply_text(welcome_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /help is issued."""
    help_text = (
        "🤖 הוראות שימוש:\n\n"
        "1. שלח תמונה של קבלה\n"
        "2. הבוט יסרוק ויחלץ את המידע\n"
        "3. אם חסר מידע, הבוט יבקש ממך להשלים\n"
        "4. הנתונים יישמרו אוטומטיקה\n\n"
        "💡 טיפים:\n"
        "• וודא שהתמונה ברורה\n"
        "• הקבלה צריכה להיות מלאה\n"
        "• תאורה טובה משפרת תוצאות\n"
        "• אם הבוט לא מזהה נתונים, תוכל להכניס אותם ידנית\n\n"
        "📊 פקודות נוספות:\n"
        "/payments - הצג את כל התשלומים שלי\n"
        "/raw - הצג טקסט גולמי מהקבלה האחרונה\n"
        "ביטול - עצור תהליך השלמת נתונים"
    )
    await update.message.reply_text(help_text)


async def show_payments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all payments for the user."""
    user_id = update.message.from_user.id
    payments = get_user_payments(user_id)

    if not payments:
        await update.message.reply_text("🔍 לא נמצאו תשלומים עבורך עדיין.\nשלח תמונה של קבלה כדי להתחיל!")
        return

    response = f"📊 **התשלומים שלך ({len(payments)} סה\"כ):**\n\n"

    total_spent = 0
    for payment in payments:
        date_str = payment['date'].strftime('%d/%m/%Y')
        price = float(payment['price'])
        total_spent += price

        response += f"🏢 **{payment['company']}**\n"
        response += f"📅 {date_str} | 💰 {price:.2f} ₪\n\n"

    response += f"💳 **סה\"כ הוצאות:** {total_spent:.2f} ₪"

    # Split message if too long
    if len(response) > 4000:
        await update.message.reply_text(
            f"📊 **סיכום התשלומים:**\n💳 סה\"כ הוצאות: {total_spent:.2f} ₪\n📈 מספר תשלומים: {len(payments)}")

        # Send recent payments
        recent_payments = payments[:10]  # Show last 10
        recent_response = f"📋 **10 התשלומים האחרונים:**\n\n"
        for payment in recent_payments:
            date_str = payment['date'].strftime('%d/%m/%Y')
            price = float(payment['price'])
            recent_response += f"🏢 {payment['company']} | 📅 {date_str} | 💰 {price:.2f} ₪\n"

        await update.message.reply_text(recent_response)
    else:
        await update.message.reply_text(response, parse_mode='Markdown')


async def process_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle receipt images sent by users."""
    try:
        # Send initial message
        processing_msg = await update.message.reply_text("⏳ מעבד את הקבלה...")

        # Get the largest photo
        photo_file = await update.message.photo[-1].get_file()

        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
            tmp_path = tmp_file.name

        # Download the photo
        await photo_file.download_to_drive(tmp_path)

        # Process the receipt
        result = process_receipt(tmp_path)

        # Delete temporary file
        os.unlink(tmp_path)

        # Format response
        if "error" in result:
            await processing_msg.edit_text(
                f"❌ שגיאה בעיבוד הקבלה:\n{result['error']}\n\n"
                "נסה לשלוח תמונה ברורה יותר."
            )
        else:
            company = result.get('company', 'לא נמצא')
            date_str = result.get('date', 'לא נמצא')
            total_str = result.get('total', 'לא נמצא')

            # Parse the extracted data
            parsed_price = parse_price(total_str)
            parsed_date = parse_date(date_str)
            user_id = update.message.from_user.id

            # Format total with currency symbol
            if total_str and total_str != 'לא נמצא':
                total_display = f"{total_str} ₪"
            else:
                total_display = total_str

            # Check for missing data and ask user for input
            missing_data = []
            if company == 'לא נמצא' or not company:
                missing_data.append('company')
            if parsed_price is None:
                missing_data.append('price')
            if date_str == 'לא נמצא':
                missing_data.append('date')

            # Store receipt data in context for later completion
            context.user_data['pending_receipt'] = {
                'company': company if company != 'לא נמצא' else None,
                'date': date_str if date_str != 'לא נמצא' else None,
                'price': total_str if total_str != 'לא נמצא' else None,
                'parsed_price': parsed_price,
                'parsed_date': parsed_date,
                'missing_data': missing_data,
                'user_id': user_id
            }

            if missing_data:
                # Display what was found and what's missing
                response = "✅ **פרטי הקבלה:**\n\n"
                response += f"🏢 **עסק:** {company}\n"
                response += f"📅 **תאריך:** {date_str}\n"
                response += f"💰 **סכום:** {total_display}\n\n"

                # Ask for missing data
                missing_text = get_missing_data_text(missing_data[0])
                response += f"⚠️ **חסר מידע!**\n{missing_text}"

                context.user_data['waiting_for'] = missing_data[0]
                await processing_msg.edit_text(response, parse_mode='Markdown')
            else:
                # All data found, save directly
                if insert_payment(user_id, company, parsed_date, parsed_price):
                    db_status = "\n✅ **הנתונים נשמרו בהצלחה!**"
                else:
                    db_status = "\n⚠️ **שגיאה בשמירת הנתונים**"

                response = (
                    "✅ **פרטי הקבלה:**\n\n"
                    f"🏢 **עסק:** {company}\n"
                    f"📅 **תאריך:** {date_str}\n"
                    f"💰 **סכום:** {total_display}\n"
                    f"{db_status}"
                )
                await processing_msg.edit_text(response, parse_mode='Markdown')

            # Add option to see raw text
            if result.get('raw_text'):
                await update.message.reply_text(
                    "💡 רוצה לראות את הטקסט המלא? שלח /raw",
                    reply_to_message_id=update.message.message_id
                )
                # Store raw text in context for later use
                context.user_data['last_raw_text'] = result['raw_text']

    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        await update.message.reply_text(
            "❌ אירעה שגיאה בעיבוד התמונה.\n"
            "וודא שזו תמונה של קבלה ונסה שוב."
        )


async def show_raw_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the raw OCR text from the last processed receipt."""
    raw_text = context.user_data.get('last_raw_text')

    if raw_text:
        # Split into chunks if text is too long
        max_length = 4000
        if len(raw_text) > max_length:
            chunks = [raw_text[i:i + max_length] for i in range(0, len(raw_text), max_length)]
            for i, chunk in enumerate(chunks):
                await update.message.reply_text(
                    f"📄 **טקסט גולמי (חלק {i + 1}/{len(chunks)}):**\n```\n{chunk}\n```",
                    parse_mode='Markdown'
                )
        else:
            await update.message.reply_text(
                f"📄 **טקסט גולמי:**\n```\n{raw_text}\n```",
                parse_mode='Markdown'
            )
    else:
        await update.message.reply_text("אין טקסט זמין. שלח תמונה של קבלה קודם.")


async def handle_non_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text user messages like greetings or questions."""
    # First check if we're waiting for missing data input
    if await process_missing_data_input(update, context):
        return

    text = update.message.text.lower().strip()

    if text in ['היי', 'שלום', 'hi', 'hey']:
        user_first_name = update.message.from_user.first_name or "😊"
        await update.message.reply_text(f"היי {user_first_name}!")
    elif text in ['נתונים']:
        await show_payments(update, context)
    elif text in ['ביטול', 'cancel', 'עצור']:
        # Allow user to cancel the missing data process
        context.user_data.pop('waiting_for', None)
        context.user_data.pop('pending_receipt', None)
        await update.message.reply_text("✅ הפעולה בוטלה. שלח תמונה חדשה של קבלה כדי להתחיל מחדש.")
    elif 'תודה' in text:
        await update.message.reply_text("בשמחה! 😊 אם יש לך עוד קבלה, שלח לי אותה.")
    else:
        if context.user_data.get('waiting_for'):
            await update.message.reply_text("❌ לא הבנתי את הקלט. נסה שוב או כתוב 'ביטול' כדי לעצור.")
        else:
            await update.message.reply_text("לא הבנתי... נסה לשלוח קבלה 📷 או כתוב /help לעזרה.")


def main():
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("payments", show_payments))
    application.add_handler(CommandHandler("raw", show_raw_text))
    application.add_handler(MessageHandler(filters.PHOTO, process_image))
    application.add_handler(MessageHandler(~filters.PHOTO & ~filters.COMMAND, handle_non_photo))

    # Run the bot
    print("🤖 Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()