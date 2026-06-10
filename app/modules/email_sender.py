import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

MAILTRAP_HOST = os.getenv("MAILTRAP_HOST")
MAILTRAP_PORT = int(os.getenv("MAILTRAP_PORT", 2525))
MAILTRAP_USERNAME = os.getenv("MAILTRAP_USERNAME")
MAILTRAP_PASSWORD = os.getenv("MAILTRAP_PASSWORD")

def send_email(to_email: str, subject: str, message: str) -> bool:
    try:
        msg = MIMEMultipart()
        msg['From'] = MAILTRAP_USERNAME
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(message, 'plain', 'utf-8'))

        server = smtplib.SMTP(MAILTRAP_HOST, MAILTRAP_PORT)
        server.starttls()
        server.login(MAILTRAP_USERNAME, MAILTRAP_PASSWORD)
        server.send_message(msg)
        server.quit()

        print(f"Email успешно испратен до {to_email}")
        return True

    except Exception as e:
        print(f"Грешка: {e}")
        return False