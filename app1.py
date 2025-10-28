from dash import Dash, html, dcc

app = Dash(__name__)

app.layout = html.Div([
    html.H1("GCS 화면 통합"),

    dcc.Tabs([
        dcc.Tab(label='Mission Planner', children=[
            html.Div([
                html.Iframe(
                    src="http://localhost:10000",
                    width="1200",
                    height="800",
                    style={'border': 'none'}
                )
            ])
        ]),

        dcc.Tab(label='QGroundControl', children=[
            html.Div([
                html.Iframe(
                    src="http://localhost:10100",
                    width="1200",
                    height="800",
                    style={'border': 'none'}
                )
            ])
        ])
    ])
])

if __name__ == '__main__':
    app.run(debug=True, port=8000)
