import argparse

from theremin.app import main as theremin_main
from theremin.dj_app import main as dj_main
from theremin.drum_app import main as drum_main
from theremin.servo_app import main as servo_main
from theremin.sampler_app import main as sampler_main


def main() -> None:
    parser = argparse.ArgumentParser(prog="theremin")
    parser.add_argument(
        "--sampler",
        action="store_true",
        help="Launch rhythm sampler (DJ channel fader mode)",
    )
    parser.add_argument(
        "--dj",
        action="store_true",
        help="Launch Air DJ (beat-quantized mixer)",
    )
    parser.add_argument(
        "--drum",
        action="store_true",
        help="Launch sonar drum (10 cm threshold = hit, each side different sound)",
    )
    parser.add_argument(
        "--serva",
        action="store_true",
        help="Launch servo sliders (PCA9685 on serial)",
    )
    args = parser.parse_args()
    if args.serva:
        servo_main()
    elif args.drum:
        drum_main()
    elif args.dj:
        dj_main()
    elif args.sampler:
        sampler_main()
    else:
        theremin_main()


if __name__ == "__main__":
    main()
