import sys
import os

# Add project root to path to find 'app'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def check_imports():
    print("\n🔍 Verifying Project Dependencies...\n")
    missing = []
    
    # 1. External Dependencies
    # tuple: (module_name, display_name)
    params = [
        ("fastapi", "FastAPI"),
        ("uvicorn", "Uvicorn"),
        ("httpx", "HTTPX"),
        ("pydantic", "Pydantic"),
        ("pydantic_settings", "Pydantic Settings"),
        ("dotenv", "Python Dotenv"),
        ("requests", "Requests"),
        ("PIL", "Pillow"),
    ]
    
    for module, name in params:
        try:
            __import__(module)
            print(f"  ✅ {name} found")
        except ImportError:
            print(f"  ❌ {name} MISSING")
            missing.append(name)
            
    # 2. Internal Modules
    print("\n🔍 Verifying Internal Modules...\n")
    try:
        import app.main
        import app.config
        from app.services import baserow, openrouter, freepik, kieai
        from app.pipelines import stage_1_upscale, stage_2_model_gen, stage_4_angles, stage_5_video
        print("  ✅ App Modules found")
    except ImportError as e:
        print(f"  ❌ Internal Import Error: {e}")
        missing.append("Internal Modules")

    if missing:
        print(f"\n⛔ Verification FAILED. Missing: {', '.join(missing)}")
        sys.exit(1)
    else:
        print("\n✨ All checks passed! Environment is ready.\n")

if __name__ == "__main__":
    check_imports()
