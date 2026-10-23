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


## Round 2: why procedural was not enough, and what replaced it

Feedback after round 1: walking "slightly better", running "funny", ponytail "still pointing up".

* **Running looked wrong** because a sine-based cycle has none of the things that make a run
  read as a run: asymmetric stance/flight timing, a teardrop foot path, trunk twist, arm
  fold that follows the swing. Tuning numbers cannot fix that, so locomotion, sitting,
  standing and jumping now come from professionally keyframed clips (Quaternius' Universal
  Animation Library, CC0) retargeted onto the rig: `anim_lab/retarget.py`.
  * pelvis / spine / head: absolute rotation relative to rest is copied; pelvis translation
    scaled by leg length. Arms: directions are copied. Legs: IK targets + ankle pitch/yaw.
  * Natural speeds were *measured* from the clips' own planted-foot sweep (walk 0.84 m/s,
    jog 5.3 m/s, sprint 8.1 m/s), not taken from the root-motion export.
* **Speed changes** blend loops with cadence and stride solved so ground speed matches
  (`mocap_sequencer._blend_info`); below walking speed the stride shrinks so stops end in a stand.
* **Foot locking on every pose**: a planted sole is latched in world space; the ankle rolls
  about heel/ball on top of the latched base. If a foot lags its target by more than 14 cm,
  it takes a corrective step while the other stays planted (this is what lets clips that
  start in different stances chain together). The pelvis lowers if a leg would overstretch.
* **Hair**: the ponytail chain is world-referenced (gravity keeps it hanging however the
  head pitches), with gentle spring swing from head acceleration and a little drag-streaming
  at speed. Before, springs were relative to the head and could swing the tip upward.


## Round 3: fixing what the close-ups showed (feet, sit, jump)

Feedback: foot movement odd when walking, "wobbles" when running; sitting posture / sit-down
and the jump looked weird. Method this time: render *consecutive* frames close up
(`tools/render_frames.py`) and print per-frame numbers (`tools/check_foot_jitter.py`).

* **Foot wobble (root cause: a retargeting bug).** Ankle yaw was taken from the direction of
  the foot bone. When the foot points almost straight down (toe-off, running) that direction
  is numerically meaningless and flipped between -180 and +180 degrees, spinning the ankle.
  Yaw now comes from the foot's lateral axis, which stays horizontal at any pitch
  (frame-to-frame yaw change: hundreds of degrees -> under 5). Foot pitch/yaw are also
  smoothed (binomial 5-tap) to remove single-frame flicks in the source.
* **Running** is played at the clip's own natural speed (4.95 m/s) with foot-locking off above
  2 m/s: contacts last only 3-4 frames there and locking them made the legs bunch.
* **Sit-down:** the head copied the trunk's absolute orientation, so it bowed toward the
  floor as the trunk folded. 60% of the head's pitch is now cancelled (gaze stays level).
* **Jump:** take-off and touch-down frames are read from the clips' own foot data. Before, the
  legs tucked ~0.4 s before the body left the ground, the airborne period was ~1 s (a
  game-style 1 m jump), and the Land clip is a deep, heavy crouch. Now the arc is ballistic
  over a 0.52 s airtime between the real take-off and touch-down frames, the Land clip is
  started so its cross-fade finishes at touch-down, and its crouch depth is halved.
* **Impossible ankle in the air (found from a screenshot).** The run's heel-kick folded the
  foot forward under the shin: `tools/check_ankle_angle.py` measured the ankle (shin-to-foot)
  at up to 172 degrees; a human ankle tops out near 140. Cause: foot orientation was forced to
  the source's *world* pitch, which is only meaningful when the sole is on the ground - with
  different leg proportions (and IK solving the knee separately) the same world pitch becomes
  an impossible ankle bend. Fix: record the ankle rotation *relative to the shin* from the
  source and use it in the air, blending to the world-referenced pitch as the sole nears the
  ground (planted -> world, airborne -> relative). Run ankle range is now 68-119 degrees.
