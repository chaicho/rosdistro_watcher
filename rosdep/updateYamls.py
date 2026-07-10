import os
import requests

# List of (url, local_path) pairs to download and update
YAML_SOURCES = [
    (
        "https://raw.githubusercontent.com/ros/rosdistro/master/rosdep/base.yaml",
        os.path.join(os.path.dirname(__file__), "base.yaml"),
    ),
    (
        "https://raw.githubusercontent.com/ros/rosdistro/master/rosdep/python.yaml",
        os.path.join(os.path.dirname(__file__), "python.yaml"),
    ),
    (
       "https://raw.githubusercontent.com/ros/rosdistro/master/jazzy/distribution.yaml",
       os.path.join(os.path.dirname(__file__), "jazzy_distribution.yaml"),
    ),
]

def download_and_update_yaml(url, local_path):
    print(f"Downloading {url} ...")
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        with open(local_path, "w", encoding="utf-8") as f:
            f.write(response.text)
        print(f"Updated {local_path}")
    except Exception as e:
        print(f"Failed to update {local_path}: {e}")

def update_all_yamls():
    for url, local_path in YAML_SOURCES:
        download_and_update_yaml(url, local_path)

if __name__ == "__main__":
    update_all_yamls()
