import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

def log_info(msg):
    logging.info(msg)

def log_error(msg):
    logging.error(msg)
