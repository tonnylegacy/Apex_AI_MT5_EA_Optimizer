//+------------------------------------------------------------------+
//|  TradeLogger.mqh                                                  |
//|  Lightweight per-trade MAE/MFE logger for MT5 Strategy Tester    |
//|  Drop into: MQL5/Include/TradeLogger.mqh                         |
//|                                                                    |
//|  Integration (2 steps in your EA):                                |
//|    1. #include <TradeLogger.mqh>   // top of your EA file         |
//|    2. TL_OnTick();                 // inside OnTick()              |
//|                                                                    |
//|  Output CSV is written to MQL5/Files/TradeLog_<EA>_<Symbol>.csv  |
//+------------------------------------------------------------------+
#property strict

//--- Configuration (override before including if needed)
#ifndef TL_MFE_THRESHOLD_PIPS
   #define TL_MFE_THRESHOLD_PIPS 0.0   // minimum MFE to record (0 = record all)
#endif
#ifndef TL_MAX_TRACKED
   #define TL_MAX_TRACKED 256           // max simultaneously open trades tracked
#endif

//--- Internal state per tracked position
struct TL_TradeState
{
   ulong    ticket;
   datetime open_time;
   double   open_price;
   double   sl;
   double   tp;
   double   lot_size;
   int      direction;    // 1=buy, -1=sell
   double   mfe_price;    // most favourable price seen
   double   mae_price;    // most adverse price seen
   bool     active;
};

static TL_TradeState TL_Positions[TL_MAX_TRACKED];
static int           TL_Count       = 0;
static int           TL_FileHandle  = INVALID_HANDLE;
static bool          TL_Initialized = false;
static string        TL_FilePath    = "";

//--- Point-to-pip conversion helper
double TL_PipSize()
{
   double ps = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   int    digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   // For 5-digit brokers, 1 pip = 10 points; for 2-digit (XAUUSD etc), 1 pip = 1 point
   if(digits == 3 || digits == 5) return ps * 10.0;
   return ps;
}

//--- Internal: open (or reopen) the CSV file
bool TL_OpenFile()
{
   if(TL_FileHandle != INVALID_HANDLE) return true;

   string ea_name = MQLInfoString(MQL_PROGRAM_NAME);
   TL_FilePath = ea_name + "_" + _Symbol + "_TradeLog.csv";

   TL_FileHandle = FileOpen(TL_FilePath,
                            FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_SHARE_READ,
                            ',');
   if(TL_FileHandle == INVALID_HANDLE)
   {
      Print("[TradeLogger] ERROR: Cannot open file '", TL_FilePath,
            "' error=", GetLastError());
      return false;
   }

   // Write CSV header
   FileWrite(TL_FileHandle,
             "ticket","open_time","close_time",
             "direction","open_price","close_price",
             "sl","tp","lot_size",
             "mfe_pips","mae_pips",
             "net_pips","net_money",
             "duration_minutes",
             "commission","swap");
   return true;
}

//--- Internal: find slot index for a ticket (-1 = not found)
int TL_FindSlot(ulong ticket)
{
   for(int i = 0; i < TL_Count; i++)
      if(TL_Positions[i].ticket == ticket && TL_Positions[i].active)
         return i;
   return -1;
}

//--- Internal: register a newly opened position
void TL_RegisterPosition(ulong ticket)
{
   if(TL_Count >= TL_MAX_TRACKED) return;      // overflow guard

   if(!PositionSelectByTicket(ticket)) return;

   TL_TradeState &s = TL_Positions[TL_Count];
   s.ticket     = ticket;
   s.open_time  = (datetime)PositionGetInteger(POSITION_TIME);
   s.open_price = PositionGetDouble(POSITION_PRICE_OPEN);
   s.sl         = PositionGetDouble(POSITION_SL);
   s.tp         = PositionGetDouble(POSITION_TP);
   s.lot_size   = PositionGetDouble(POSITION_VOLUME);
   s.direction  = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
   s.mfe_price  = s.open_price;
   s.mae_price  = s.open_price;
   s.active     = true;
   TL_Count++;
}

//--- Internal: flush a closed trade to CSV
void TL_FlushClosed(int idx)
{
   if(!TL_OpenFile()) return;

   TL_TradeState &s = TL_Positions[idx];

   // Retrieve closed deal data from history
   if(!HistorySelectByPosition(s.ticket)) return;
   int deals = HistoryDealsTotal();
   if(deals < 2) return;  // need at least open + close deal

   // Find the closing deal (last deal in history for this position)
   ulong  close_deal   = 0;
   double close_price  = 0;
   double net_money    = 0;
   double commission   = 0;
   double swap_val     = 0;
   datetime close_time = 0;

   for(int d = deals - 1; d >= 0; d--)
   {
      ulong deal_ticket = HistoryDealGetTicket(d);
      if(HistoryDealGetInteger(deal_ticket, DEAL_ENTRY) == DEAL_ENTRY_OUT ||
         HistoryDealGetInteger(deal_ticket, DEAL_ENTRY) == DEAL_ENTRY_INOUT)
      {
         close_deal   = deal_ticket;
         close_price  = HistoryDealGetDouble(deal_ticket, DEAL_PRICE);
         net_money    = HistoryDealGetDouble(deal_ticket, DEAL_PROFIT);
         commission   = HistoryDealGetDouble(deal_ticket, DEAL_COMMISSION);
         swap_val     = HistoryDealGetDouble(deal_ticket, DEAL_SWAP);
         close_time   = (datetime)HistoryDealGetInteger(deal_ticket, DEAL_TIME);
         break;
      }
   }
   if(close_deal == 0) return;

   double pip      = TL_PipSize();
   double mfe_pips = (s.mfe_price - s.open_price) * s.direction / pip;
   double mae_pips = (s.open_price - s.mae_price) * s.direction / pip;
   double net_pips = (close_price  - s.open_price) * s.direction / pip;
   int    dur_min  = (int)((close_time - s.open_time) / 60);

   FileWrite(TL_FileHandle,
             (string)s.ticket,
             TimeToString(s.open_time,  TIME_DATE|TIME_MINUTES),
             TimeToString(close_time,   TIME_DATE|TIME_MINUTES),
             (s.direction == 1 ? "buy" : "sell"),
             DoubleToString(s.open_price, _Digits),
             DoubleToString(close_price,  _Digits),
             DoubleToString(s.sl,         _Digits),
             DoubleToString(s.tp,         _Digits),
             DoubleToString(s.lot_size,   2),
             DoubleToString(MathMax(0, mfe_pips), 2),
             DoubleToString(MathMax(0, mae_pips), 2),
             DoubleToString(net_pips,  2),
             DoubleToString(net_money, 2),
             (string)dur_min,
             DoubleToString(commission, 2),
             DoubleToString(swap_val,   2));

   FileFlush(TL_FileHandle);   // flush after each trade — safe even if tester aborts
}

//+------------------------------------------------------------------+
//|  TL_OnTick() — Call this inside your EA's OnTick()               |
//+------------------------------------------------------------------+
void TL_OnTick()
{
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

   // --- Update running MFE/MAE for all tracked positions ---
   for(int i = 0; i < TL_Count; i++)
   {
      if(!TL_Positions[i].active) continue;

      ulong ticket = TL_Positions[i].ticket;

      // Check if position is still open
      if(!PositionSelectByTicket(ticket))
      {
         // Position closed — flush to CSV then deactivate
         TL_FlushClosed(i);
         TL_Positions[i].active = false;
         continue;
      }

      double current_price = (TL_Positions[i].direction == 1) ? bid : ask;

      // Update MFE (best price in trade direction)
      if(TL_Positions[i].direction == 1)   // BUY: higher is better
         TL_Positions[i].mfe_price = MathMax(TL_Positions[i].mfe_price, current_price);
      else                                  // SELL: lower is better
         TL_Positions[i].mfe_price = MathMin(TL_Positions[i].mfe_price, current_price);

      // Update MAE (worst price against trade direction)
      if(TL_Positions[i].direction == 1)   // BUY: lower is worse
         TL_Positions[i].mae_price = MathMin(TL_Positions[i].mae_price, current_price);
      else                                  // SELL: higher is worse
         TL_Positions[i].mae_price = MathMax(TL_Positions[i].mae_price, current_price);
   }

   // --- Register any newly opened positions not yet tracked ---
   int total = PositionsTotal();
   for(int p = 0; p < total; p++)
   {
      ulong ticket = PositionGetTicket(p);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;

      if(TL_FindSlot(ticket) == -1)
         TL_RegisterPosition(ticket);
   }
}

//+------------------------------------------------------------------+
//|  TL_Deinit() — Optionally call in OnDeinit() to close file       |
//+------------------------------------------------------------------+
void TL_Deinit()
{
   if(TL_FileHandle != INVALID_HANDLE)
   {
      FileClose(TL_FileHandle);
      TL_FileHandle = INVALID_HANDLE;
   }
   Print("[TradeLogger] Log written to: ", TL_FilePath);
}
//+------------------------------------------------------------------+
