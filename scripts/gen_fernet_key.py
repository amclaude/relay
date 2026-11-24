"""Print a new Fernet key.

Usage:
    python scripts/gen_fernet_key.py

Copy the output into the FERNET_KEY variable in your .env file.
Keep the key secret. If you lose it, stored refresh tokens cannot be decrypted.
"""

from cryptography.fernet import Fernet


def main() -> None:
    print(Fernet.generate_key().decode())


if __name__ == "__main__":
    main()
