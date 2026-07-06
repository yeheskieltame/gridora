"use client";

// Landing hero — full-bleed background video blended into the #EDEEF5 base,
// two-tone slide-up headline with the inline pill-eye, integrated search capsule,
// and the architectural edge anchors. Follows the approved landing spec exactly.
import { motion } from "motion/react";

const VIDEO_URL =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260603_132049_036591b8-6e92-4760-b94c-a7ea6eef315c.mp4";

function EyePill() {
  return (
    <span className="w-[16px] md:w-[42px] lg:w-[62px] h-[0.62em] border-[2px] border-[#1a1a1a] rounded-full inline-flex items-center justify-center align-middle mx-1">
      <span className="w-2 h-2 rounded-full bg-[#1a1a1a]" />
    </span>
  );
}

export function LandingHero() {
  return (
    <section className="relative min-h-[110vh] sm:min-h-[140vh] w-full flex flex-col items-center justify-start overflow-hidden bg-bg-base">
      {/* background video, masked into the page base */}
      <div className="absolute top-[15vh] sm:top-[20vh] left-0 w-full h-[95vh] sm:h-[120vh] z-0 pointer-events-none">
        <video
          autoPlay
          loop
          muted
          playsInline
          className="w-full h-full object-cover opacity-100"
          src={VIDEO_URL}
        />
        <div className="absolute top-0 left-0 w-full h-24 sm:h-32 bg-gradient-to-b from-bg-base to-transparent" />
      </div>

      {/* headline + search */}
      <div className="max-w-7xl w-full mx-auto px-8 md:px-16 lg:px-20 relative z-10 grid grid-cols-12 gap-x-4 md:gap-x-8 pt-36 md:pt-44">
        <div className="col-span-12 md:col-span-10 md:col-start-2">
          <motion.h1
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8 }}
            className="font-outfit text-[clamp(2rem,5.2vw,4.25rem)] leading-[1.08] tracking-tight"
          >
            <span className="text-[#1a1a1a]">Gridora is an autonomous</span>{" "}
            <span className="text-[#8e8e8e]">trading agent</span>
            <br />
            <span className="text-[#8e8e8e]">that proves every trade on-chain</span>
            <br />
            <span className="text-[#8e8e8e]">
              and never <EyePill /> holds your keys.
            </span>
          </motion.h1>

          <motion.div
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.15 }}
            className="mt-8 max-w-md"
          >
            <form
              action="/console"
              className="bg-white rounded-[6px] border border-black/[0.05] p-1 pl-4 flex items-center shadow-sm"
            >
              <input
                name="q"
                placeholder="Ask the agent anything..."
                className="flex-1 bg-transparent outline-none text-[15px] text-[#1a1a1a] placeholder:text-[#8e8e8e] font-sans"
              />
              <button
                type="submit"
                aria-label="open the console"
                className="bg-[#1a1a1a] text-white w-9 h-9 rounded-full relative shrink-0"
              >
                <svg
                  viewBox="0 0 24 24"
                  className="w-4 h-4 absolute inset-0 m-auto"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M5 12h14M13 6l6 6-6 6" />
                </svg>
              </button>
            </form>
          </motion.div>
        </div>
      </div>

      {/* edge anchors */}
      <div className="absolute right-4 md:right-8 top-1/2 -translate-y-1/2 z-10">
        <div className="bg-white/40 backdrop-blur-md border border-white/50 rounded-full px-4 py-2 text-[12px] lowercase text-[#1a1a1a] shadow-sm">
          id — <span className="font-semibold">en</span>
        </div>
      </div>
      <div className="absolute bottom-6 left-6 md:left-10 z-10 text-[12px] text-[#1a1a1a]/60 font-sans">
        2026
      </div>
      <div className="absolute bottom-6 right-6 md:right-10 z-10 text-[12px] lowercase text-[#1a1a1a]/60 font-sans">
        verifiable trading agents
      </div>
    </section>
  );
}
