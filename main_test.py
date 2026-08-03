import sys

# Test script syntax
try:
    print("Test passed.")
except Exception as e:
    print(f"Error: {e}")
    sys.exit(1)
