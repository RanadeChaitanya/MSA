import base64, json, re, shutil, subprocess, sys, zlib
from pathlib import Path

PAYLOAD = (
    "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"
    "M1AofIRWbeHaKS9bZiaF59Q9U4cp5cfpFEisfodoViLcyheOjQKldhD1KNemLQkyBvMqrOFO/dOvBd5nOk0Spw+L0AcXlC8OGI8y"
    "sZKwwswCWe4D2WJ2I1tsrlv5xnoXhjIuMCWj+Nz5NXzUHjWJjQrJjoMZCwHys7vqEArkDuN8DDp3ApfUuHwAm2biAZ59q0SpuUVJ"
    "GYH5+fX62I5rbM/8IBZdh879cKpngzS1sAaGp4NeeJ9a19cHKGNymJvRmTzDnZ86gk42BOjj4WYzLwom5M8mcfE/tLUKdJzhEoFz"
    "bH589kzMyCfzvxh4JL7BD51iA+IHzKO7EK8nAWVQ7k88Xy7zZQfAFLNHcBbWmtYK7fvoanZE7nV28HitO2OCPtQFTIZHVqWvvMHM"
    "MWM2Spv1S413s5U220u4s/A664A3163E/9AONp4G8v3s5L3P9L72A153f36q5z7vN73u/f135kHvL41+8f98iP8dff4Lq3X/v1q7"
    "7324S00dCrt/aE1O4f//2dC7x/2pA9884a/f5m1Aed/1d343oT56eXqC54D4q4X4+O1+32I93sD9Erm1G1r3m3XbM68/DnoX3rXw"
    "7X0P++T3X8C4q8V/A2939/53O2j28v3D8B0sO2O1G/S11gM24K17A84P03m0097fMIn/9eJvf+Svh6/3L347p9//y3v0pP49rO2B"
    "eXNvdA/fve5Cff6f4Yf/8I1zL+L/eD2S/9Ue/3d13v8X73N3E35N3S24u41/4x/0f1R/Xv35eP/vJ809vX24u3/9i773L9/e/Tf3"
    "qSngD//v/eJ1e8+bO4L/73iTvgM8/x58+A//4UeI/yO4e++e8e9D+P0XvAnmO8E0m2oGze7Nf9X4zXvwq4H2A/2CgAeeBv/eG3/7"
    "D+/3e4C++N3f338FfwE434kP3w23wDfev+O/9oXfvv3pE9y9/T3/83c83gR/3O9Cff7v/sA39+/f8V/7/Bvc33f81/7Aew/f4yee"
    "3Xf3xI32eXcfvL3ne/yP4M7dPT/vfv2mGz10q9H394v3b+8e8f85f3v/5rf3x4d4d+90uPvv3b+/d3A0vHp0/2qgdSveN794L3/x"
    "G30f3j374/3s/eL/01v4e91r+/eL3uB/B30P282j++D3w19d0qGfvg3+328X3g/9+/vR83/3NfD+f/A13i40f32/3fX3v/sT4X/c"
    "7Pvf3g3u7m8G13eL33v1eN373r2Nvx1c3YfXze847eHfwb94v//Dvf9w9wE/uP++6+4fH328/+C1aD+7A064N4/4D0f3wYve+E9d"
    "mJsn3L/99S/8/Xfw7dD4A+J/5w/++x90k3m33T01/3aA3yU0vI5e4O9d0p/g1eL/vR1/exj8v5vx/778+3/5D3v/+/D8P/mPv/9v"
    "7fPvvP9H32j7v/fvvv++H3+e4G/d6K6p0T3e97vdv7v8Ifrv4m/4f3x/vD3j/vf8+N5+/e4G++v49+93u3/f4L7/h9/+4P/0e199"
    "f2/9H/3p7+X73b///eL26f/v/v8/fvd633u/3/7++4//H///4f87/H+f/4v9/f7f/uI2vJrd4N/6e++3d/c0fPvL1y/v/s/++vcf"
    "//vf58efN/vv+vv09oU/GvwvX907aA5++I+/Nf+O7p7c4U/Xw5v7+z++/6N3sHff8d85f+8/4A34Svf/e/+83y1+34X2I/z9v/D/"
    "3f//228f419d8J96G54/1b9z11d331k3m278xV/+/O0Xf2Xv/+Cvf/4m/4/3/1L3/5Svd/4u//3f7z940f8s39++34//3f4//vvF"
    "b/219X/33+D//91/735sfnf4e24P/0f/f8D7f3O4Xw282f6v/x/6/x3y/x/891//f//+x5+O3v23e0/f/Nf+Pvwf/e8j+D31/4/w"
    "/5v+v7v37x/u/m3A//vvDvdv3/725+4Lff9/9+L9b++/A+/7+6+/0vffBveP/P9//4+2/379P/43f/3uN/x31e5P/3s3fA//A333"
    "m9X/++D/+/7/3d3d+3f4+/+T9u3B1/f31/93/9I834Xvfx/+5D/4l4/+41+932+u8f/+m9s8+u9/+N7eA24d/vv6xvdD93sP3z/6"
    "/rf422/+7799+s+/eTdf33u6/28/v/j/9rX3/9/e/Tf39e++e29/e5q/e4L/2fvxX/770f3f8C9//m9f//5/9L/++4f/39294C++"
    "4M11u+9/+P+x2+m++/++A57v0D3d3d8+4b3/h//vef37b98+3N334P4e//3b9/v/9fO4+79v/2++/1//4C+/e/H9e9///tf8ffjh"
    "/v/+4Rvw2/f/g3/3p/jvf+13/v9/6L/+N9f55eL2v3mC+H8P/I/e++4/3f19eI2X+1//s9vwfO2e9n9e/y/vvX98u/9f4H/8v19u"
    "eP8v/47/3fvfX9vf/m/d26N3j/ev4S/x11/i/4/A+3939z3s1b+4++/f409++5f4v/m//33v13c/uL++ef7v7q3v5/fe3a9/9+0N"
    "+v/gfv6O8e++/vU+/1++/63++/9A44/f92///f/p/3b5v/7e/j3i/p1+/5fX/vf79z/i92/vfvE+e0/f3w1//e99v/9r4Xvfvvv7"
    "a//9/ff3L/7fD9/e82+//e/3b99/4//f0yv48d7vf+/9O4JvwvNfw3cAn43+e334/3/9C3//334P/vff/A3++984ffx+/x9+e4p3"
    "e/rX//H7b2s36v/vX/30/Pvvvn0P4b4Xf34L3sL/+y++/9/9f/p/3eC///3ffuP+/7tf9P1/+/4/f/m+A5/u+/A///3f0O+/3941"
    "A3g+vO82f/+19/yH8P+34f9/+P9eA9/63/0O4P/5vvv3A36L/4fA+8D83xvf3f/+9vv93f+X4Ntfvv/q4S///4f4e/v++4326f/3"
    "4TvgfvA0xL25u/sOf1/e94v+/f6v//3e0319e7/fAfdP/++X+m/f/0vfP9P+52/+N77w/S+/3f9vv+/99vT5+3+/uPvvv/f/43+/"
    "+0Pff433D/f4v2+//v5f/uM3eN3u/g6X9vfz+vC42/f4+/++40++/8///03ePvv5e+3bN+034E3v3d3+X954P1+8//vP34L/1y9/"
    "fv9++/4e/r43eN+A9270P2/87jvh3+9/w+/444fe+eXv8ffA7f2f//vvf82/vwO+m3///v/3b//ffvv9/v//A7a3290="
)

root = Path.cwd()
if not (root / "pipeline" / "stages").is_dir():
    sys.exit("Run this from the project root (the folder that contains 'pipeline').")
spec = json.loads(zlib.decompress(base64.b64decode("".join(PAYLOAD.split()))).decode("utf-8"))

def read(p):
    raw = p.read_bytes().decode("utf-8")
    return raw.replace("\r\n", "\n"), "\r\n" in raw

plans, problems = {}, []
for rel, s in spec.items():
    p = root / rel
    if "new" in s:
        plans[rel] = (s["new"], False)
        continue
    if not p.exists():
        problems.append(rel + ": file not found"); continue
    text, crlf = read(p)
    done = 0
    for old, new in s["hunks"]:
        if text.count(old) == 1:
            text = text.replace(old, new, 1)
        elif old not in text and new in text:
            done += 1
        else:
            problems.append(rel + ": a hunk does not match: " + repr(old[:70]))
    plans[rel] = (text, crlf)
    if done == len(s["hunks"]):
        print("already patched:", rel)
if problems:
    sys.exit("NOTHING WAS CHANGED.\n" + "\n".join(problems))

for rel, (text, crlf) in plans.items():
    p = root / rel
    if p.exists():
        shutil.copy2(p, str(p) + ".bak_v1_2")
    p.write_text(text.replace("\n", "\r\n") if crlf else text, encoding="utf-8", newline="")
    print("patched:", rel)

for name in (".env", ".env.example"):
    p = root / name
    if p.exists():
        t = p.read_text(encoding="utf-8")
        if re.search(r"(?m)^OPTION_IMAGE_MODE=", t):
            t2 = re.sub(r"(?m)^OPTION_IMAGE_MODE=.*$", "OPTION_IMAGE_MODE=visual_only", t)
        else:
            t2 = t.rstrip() + "\nOPTION_IMAGE_MODE=visual_only\n"
        if t2 != t:
            shutil.copy2(p, str(p) + ".bak_v1_2")
            p.write_text(t2, encoding="utf-8", newline="")
            print("updated:", name, "-> OPTION_IMAGE_MODE=visual_only")

print("\nRunning tests ...")
r = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=str(root))
print("\nTESTS PASSED" if r.returncode == 0 else "\nTESTS FAILED (backups: *.bak_v1_2)")
