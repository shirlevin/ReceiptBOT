import os
import re
import json
import pytesseract
from PIL import Image
import openai
from typing import Dict, Optional
import base64
from openai import OpenAI


# Configure Tesseract path for Windows
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Configure OpenAI
api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    raise ValueError("OPENAI_API_KEY not found in environment variables")
client = OpenAI(api_key=api_key)

def extract_text_with_tesseract(image_path: str) -> Dict[str, any]:
    """Extract text from image using Tesseract OCR with Hebrew and English support."""
    try:
        image = Image.open(image_path)
        config = r'--oem 3 --psm 6'

        text = pytesseract.image_to_string(
            image,
            lang='heb+eng',
            config=config
        )

        return {
            'success': True,
            'text': text.strip(),
            'method': 'Tesseract OCR',
            'languages': 'Hebrew + English'
        }
    except Exception as e:
        return {
            'success': False,
            'error': f"OCR Error: {str(e)}"
        }


def clean_hebrew_text(text: str) -> str:
    """Clean Hebrew text by removing Unicode control characters and OCR artifacts."""
    if not text:
        return text

    # Remove RTL/LTR marks and other directional characters
    text = re.sub(r'[\u200e\u200f\u202a-\u202e]', '', text)

    # Remove common OCR artifacts and quotes
    text = re.sub(r'[°""''""''`´]', '', text)

    # Clean up extra spaces
    text = re.sub(r'\s+', ' ', text).strip()

    return text


def extract_with_openai_vision(image_path: str) -> Dict[str, Optional[str]]:
    """Use OpenAI Vision (GPT-4o or gpt-4-vision-preview) to extract structured data directly from image."""
    try:
        with open(image_path, "rb") as image_file:
            image_bytes = image_file.read()
            encoded_image = base64.b64encode(image_bytes).decode("utf-8")

        prompt = """Extract the following information from this Hebrew receipt image:
        1. Company name (שם החברה) – usually at the top or very bottom near 'תודה'.
        2. Date – in DD/MM/YYYY format.
        3. Total amount – the largest number near 'סה"כ', 'סך הכל', 'לתשלום'.

        If data is missing, use null.
        Return only valid JSON with keys: {"company": "...", "date": "...", "total": "..."}."""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful receipt parser."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}
                        },
                    ],
                },
            ],
            max_tokens=500,
        )

        content = response.choices[0].message.content.strip()

        # Parse JSON from the response
        start = content.find('{')
        end = content.rfind('}') + 1
        if start != -1 and end != 0:
            json_str = content[start:end]
            result = json.loads(json_str)
            return {
                'company': result.get('company'),
                'date': result.get('date'),
                'total': result.get('total')
            }
        else:
            raise ValueError("No JSON found in response.")

    except Exception as e:
        print(f"OpenAI Vision error: {str(e)}")
        return {'company': None, 'date': None, 'total': None}



def parse_hebrew_receipt_fallback(text: str) -> Dict[str, Optional[str]]:
    """Fallback regex-based parser for when GPT fails."""
    text = clean_hebrew_text(text)
    lines = [clean_hebrew_text(line.strip()) for line in text.split('\n') if line.strip()]
    result = {'company': None, 'date': None, 'total': None}

    # Extract Company Name
    skip_words = ['קבלה', 'בס"ד', 'חשבונית', 'תאריך', 'שעה', 'קופה', 'עסקה', 'WT', 'לקוח יקר']

    for line in lines[:10]:
        if any(skip_word in line for skip_word in skip_words):
            continue

        if (len(line) > 3 and
                re.search(r'[\u0590-\u05FF]', line) and
                not re.search(r'^\d{1,2}[./]\d{1,2}[./]\d{2,4}', line) and
                not re.search(r'^\d+\.\d{2}', line) and
                not re.search(r'^[\d\s\-:]+$', line)):

            if (re.search(r'[\u0590-\u05FF]', line) and
                    (re.search(r'\d+', line) or len(line.split()) >= 2)):
                result['company'] = line
                break

    # Extract Date
    date_patterns = [
        r'(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})',
        r'(\d{2,4})[./\-](\d{1,2})[./\-](\d{1,2})'
    ]

    for line in lines:
        for pattern in date_patterns:
            matches = re.findall(pattern, line)
            for match in matches:
                day, month, year = match
                if len(year) == 2:
                    year = '20' + year if int(year) < 50 else '19' + year

                try:
                    if 1 <= int(day) <= 31 and 1 <= int(month) <= 12:
                        result['date'] = f"{day}/{month}/{year}"
                        break
                except ValueError:
                    continue
        if result['date']:
            break

    # Extract Total Amount
    total_keywords = ['סה"כ', 'סך הכל', 'לתשלום', 'סה״כ', 'סכום', 'total', 'סהכ']

    for i, line in enumerate(lines):
        if any(keyword in line.lower() for keyword in total_keywords):
            search_lines = lines[i:i + 3]

            for search_line in search_lines:
                amount_patterns = [
                    r'(\d+\.\d{2})\s*₪?',
                    r'(\d+)\s*₪',
                    r'₪\s*(\d+\.\d{2})',
                    r'₪\s*(\d+)'
                ]

                for pattern in amount_patterns:
                    amounts = re.findall(pattern, search_line)
                    if amounts:
                        result['total'] = max(amounts, key=lambda x: float(x))
                        break

                if result['total']:
                    break

        if result['total']:
            break

    # Fallback: Find largest amount
    if not result['total']:
        all_amounts = []
        for line in lines:
            amounts = re.findall(r'\d+\.\d{2}', line)
            for amount in amounts:
                if 1.0 <= float(amount) <= 10000.0:
                    all_amounts.append(amount)

        if all_amounts:
            result['total'] = max(all_amounts, key=lambda x: float(x))

    return result


def process_receipt(image_path: str, use_gpt: bool = True, use_vision: bool = True) -> Dict[str, any]:
    """
    Process receipt image using either OpenAI Vision or Tesseract + GPT fallback.
    """
    if not os.path.exists(image_path):
        return {"error": "Image file not found"}

    if use_gpt and use_vision:
        receipt_data = extract_with_openai_vision(image_path)
        receipt_data['extraction_method'] = 'OpenAI Vision API'
        receipt_data['raw_text'] = None  # not used
        return receipt_data

    # Fallback to OCR + GPT/regex
    ocr_result = extract_text_with_tesseract(image_path)
    if not ocr_result['success']:
        return {"error": ocr_result['error']}

    if use_gpt:
        receipt_data = extract_with_openai_vision(ocr_result['text'])
    else:
        receipt_data = parse_hebrew_receipt_fallback(ocr_result['text'])

    receipt_data['raw_text'] = ocr_result['text']
    receipt_data['extraction_method'] = 'GPT-3.5' if use_gpt else 'Regex'
    return receipt_data



def save_results(image_path: str, receipt_data: Dict[str, any]) -> str:
    """Save results to JSON file."""
    base_name = os.path.splitext(image_path)[0]
    output_file = f"{base_name}_receipt_data.json"

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(receipt_data, f, ensure_ascii=False, indent=2)

    return output_file


# Main function for standalone usage
if __name__ == "__main__":
    # Check if OpenAI API key is set
    if not openai.api_key:
        print("⚠️  Warning: OPENAI_API_KEY not set. Using regex fallback method.")
        print("For better results, set your OpenAI API key in the environment.")

    image_path = input("Enter image path: ").strip()
    result = process_receipt(image_path)

    if "error" in result:
        print(f"❌ Error: {result['error']}")
    else:
        print("\n" + "=" * 50)
        print("RECEIPT DATA EXTRACTED")
        print("=" * 50)
        print(f"Company: {result.get('company', 'Not found')}")
        print(f"Date: {result.get('date', 'Not found')}")

        total = result.get('total', 'Not found')
        if total and total != 'Not found':
            print(f"Total: {total} ₪")
        else:
            print(f"Total: {total}")

        print(f"Method: {result.get('extraction_method', 'Unknown')}")
        print("=" * 50)

        save_results(image_path, result)

        if input("\nShow raw OCR text? (y/n): ").lower() == 'y':
            print("\nRAW OCR TEXT:")
            print("-" * 40)
            print(result.get('raw_text', ''))