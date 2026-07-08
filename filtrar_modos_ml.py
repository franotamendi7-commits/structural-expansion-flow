import pickle, pandas as pd, numpy as np

with open('ml_model.pkl', 'rb') as f:
    model = pickle.load(f)
features_ok = model.feature_names_in_

for modo in ['strict', 'flexible', 'free']:
    archivo = f'resultado_{modo}.csv'
    try:
        df = pd.read_csv(archivo)
    except FileNotFoundError:
        print(f'{modo}: no se encontró {archivo}')
        continue
    if df.empty:
        print(f'{modo}: sin trades')
        continue
    # Preprocesar como siempre
    phase_dummies = pd.get_dummies(df['phase_h1'], prefix='phase')
    mom_dummies = pd.get_dummies(df['mom_direction'], prefix='mom')
    df = pd.concat([df, phase_dummies, mom_dummies], axis=1)
    df.drop(['phase_h1','mom_direction'], axis=1, inplace=True)
    cols_a_evitar = ['symbol','entry_time','direction','entry_signal',
                     'entry_real','sl_original','tp1','contracts','pnl_neto',
                     'exit_events','exit_type','explanation','score']
    X = df[[c for c in df.columns if c not in cols_a_evitar]].fillna(0)
    for col in features_ok:
        if col not in X.columns:
            X[col] = 0.0
    X = X[features_ok]
    prob = model.predict_proba(X)[:,1]
    filtro = prob > 0.55
    df_filtrado = df[filtro]
    if len(df_filtrado) == 0:
        print(f'{modo}: ML vetó todos los trades')
        continue
    wr = (df_filtrado['pnl_neto'] > 0).mean()
    total = df_filtrado['pnl_neto'].sum()
    dd = ((100 + df_filtrado['pnl_neto'].cumsum()).cummax() - (100 + df_filtrado['pnl_neto'].cumsum())).max()
    print(f'{modo} con ML: Trades={len(df_filtrado)} (de {len(df)}), WR={wr:.2%}, PnL=${total:.2f}, DD=${dd:.2f}')
