# Motion notes: what "natural" needed

After the first version the movement was *better but still not natural*, so I compared the
code against what is known about human motion. Sources are mostly teaching material and
developer write-ups, not primary studies, so I use the qualitative structure rather than any
single number.

| Finding | Source | What the code did wrong -> what it does now |
|---|---|---|
| Gait is heel strike -> foot flat -> heel rise -> toe-off ("rockers"); ankle ~neutral at strike, ~5 deg dorsiflexion at midstance, 15-20 deg plantarflexion at toe-off | [AAPMR, biomechanics of normal gait](https://now.aapmr.org/biomechanics-normal-gait/) | The foot was glued to the shin (no ankle), so it tilted with the leg. -> `Ankle` deform bones; `actions.foot_roll` / `roll_offset` roll the sole about heel then ball while the contact point stays put. |
| Knee flexes ~15 deg at loading response; hip ~30 deg at strike | same, [orthobullets](https://orthobullets.com/foot-and-ankle/7001/gait-cycle) | Pelvis is now lowered only as far as needed so neither leg over-extends (`gait_pose`, reach clamp), which yields loading-response knee flexion. |
| Pelvis rotates and the swing side drops (a few degrees each) | AAPMR; [boneandspine](https://boneandspine.com/normal-gait-cycle/) | Only bobbed before. -> pelvis list (+/-4 deg), rotation (+/-6 deg), lateral sway toward the stance foot, counter-rotating shoulders. |
| Walking has double support; running has a flight phase | gait-cycle references above | Stance/swing was exactly 50/50. -> `duty` 0.60 walking, 0.40 running; cadence rises with speed. |
| Planted feet stay latched in world space; turn-in-place keeps feet planted until the twist builds, then steps | [distance-driven gait write-up](https://github.com/danieltalbert/gradientfall/pull/5), [foot-latching notes](https://www.fab.com/listings/bdce3266-1b4e-42dc-afed-05e83a673b9e) | Feet were tied to the body, so they skated in turns, starts, stops and back-steps. -> `sequencer.plan_feet`: world-latched feet, predicted landing spots, stepping while pivoting. Measured heel/ball speed during stance: median 0.000 m/s. |
| Sit-to-stand: flexion-momentum -> seat-off -> extension -> stabilisation; feet drawn back, trunk pitches forward so the centre of mass moves over the feet | [Schenkman et al. summary](https://users.wfu.edu/marshap/MattErin.htm) | Stand-up was a plain pose blend. -> staged keys with feet pulled back, up to ~44 deg shared trunk flexion, head counter-pitch, settle; sit-down is the controlled reverse with a slight settle. |
| Lean-and-catch: weight leads before the foot moves; arms lag the torso (overlap) | [animation principles notes](https://courses.cs.washington.edu/courses/cse490j/09wi/490j_assignment4/index.html) | Lean now uses *upcoming* acceleration, the head leads turns by 0.35 s, arms lag legs by ~0.3 rad, and the gait always finishes its step into double support before idling. |

Not done (would need motion-capture data or hand animation): weight shift before sitting,
hands pushing off the seat, true turn-steps, finger/face/eye motion, cloth.
